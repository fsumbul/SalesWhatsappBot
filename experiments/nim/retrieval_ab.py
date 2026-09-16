#!/usr/bin/env python3
"""WP4 — retrieval A/B: bge-m3 vs NIM embeddings × local vs NIM reranker.

Indexes a config file into throw-away ``kb_`` graphs on FalkorDB for every
available configuration, replays the golden question set and reports
recall@1 / recall@3 / MRR / latency / FalkorDB memory delta per configuration.
It also calibrates the ADR-003 entailment threshold for every reranker on the
Turkish (claim, evidence, entailed) pairs. Run from ``apps/api``::

    poetry run python ../../experiments/nim/retrieval_ab.py \\
      --ollama-url http://127.0.0.1:11434 \\
      --nim-embed-url http://gpu:8040 --nim-rerank-url http://gpu:8041 \\
      --report ../../experiments/nim/retrieval-ab-report.json

Only synthetic/approved data (the shipped config + golden sets) is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

_HERE = Path(__file__).resolve().parent
_API = _HERE.parents[1] / "apps" / "api"
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_API))

from _common import add_common_args, assert_endpoint_allowed, latency_summary, timed, write_report  # noqa: E402
from src.integrations.embeddings import NimEmbeddingClient, OllamaEmbeddingClient  # noqa: E402
from src.integrations.reranker import CrossEncoderReranker, NimReranker, NullReranker  # noqa: E402
from src.modules.agents.company_config import CompanyAgentConfig  # noqa: E402
from src.modules.knowledge.graph_store import GraphStore  # noqa: E402
from src.modules.knowledge.indexer import KnowledgeIndexer  # noqa: E402
from src.modules.knowledge.ports import RetrievalRequest  # noqa: E402
from src.modules.knowledge.retrieval import FalkorGraphFactRetriever  # noqa: E402

BASELINE = {"recall_at_3_min": 1.0, "mrr_min": 0.96}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=_API / "config" / "arti_kasnak.production.json")
    parser.add_argument("--golden", type=Path, default=_API / "config" / "knowledge_golden.arti_kasnak.json")
    parser.add_argument("--entailment-golden", type=Path, default=_API / "config" / "entailment_golden.tr.json")
    parser.add_argument("--falkordb-host", default="localhost")
    parser.add_argument("--falkordb-port", type=int, default=6381)
    parser.add_argument("--ollama-url", default="", help="bge-m3 through Ollama (empty = skip)")
    parser.add_argument("--ollama-model", default="bge-m3")
    parser.add_argument("--nim-embed-url", default="", help="NIM embedding container (empty = skip)")
    parser.add_argument("--nim-embed-model", default="nvidia/nemotron-3-embed-1b")
    parser.add_argument("--nim-embed-dimension", type=int, default=2048)
    parser.add_argument("--nim-rerank-url", default="", help="NIM reranking container (empty = skip)")
    parser.add_argument("--nim-rerank-model", default="nvidia/llama-nemotron-rerank-vl-1b-v2")
    parser.add_argument("--local-reranker", default="BAAI/bge-reranker-v2-m3", help="'' to skip")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--k", type=int, default=5)
    add_common_args(parser)
    return parser.parse_args()


def _memory(store: GraphStore) -> int | None:
    try:
        return int(store._client().connection.info("memory")["used_memory"])  # noqa: SLF001
    except Exception:
        return None


async def _golden_metrics(retriever: FalkorGraphFactRetriever, tenant: Any, version: Any, items: list[dict[str, Any]], k: int) -> dict[str, Any]:
    hits1 = hitsk = 0
    reciprocal = 0.0
    latency: list[float] = []
    case_results: list[dict[str, Any]] = []
    for item in items:
        with timed(latency):
            result = await retriever.retrieve(RetrievalRequest(tenant_id=tenant, agent_version_id=version, query=item["question"], anchor_subject_ids=tuple(item.get("anchor_subject_ids", [])), max_candidates=k))
        ranked = list(result.fact_ids)
        expected = set(item["expected_fact_ids"])
        first = next((i for i, fact_id in enumerate(ranked) if fact_id in expected), None)
        case_results.append({"question": item["question"], "expected_fact_ids": sorted(expected),
                             "ranked_fact_ids": ranked, "first_relevant_rank": first + 1 if first is not None else None,
                             "degraded": result.degraded, "elapsed_ms": round(latency[-1], 2)})
        if first is not None:
            hitsk += 1
            reciprocal += 1 / (first + 1)
            hits1 += first == 0
    n = max(1, len(items))
    return {"n": len(items), "recall_at_1": round(hits1 / n, 3), f"recall_at_{k}": round(hitsk / n, 3), "mrr": round(reciprocal / n, 3), "latency_ms": latency_summary(latency), "case_results": case_results}


async def _entailment_calibration(reranker: Any, pairs: list[dict[str, Any]]) -> dict[str, Any]:
    scores: list[tuple[float, bool]] = []
    for pair in pairs:
        result = await reranker.rerank(pair["claim"], [pair["evidence"]])
        scores.append((result[0], bool(pair["entailed"])))
    best: dict[str, Any] = {"threshold": None, "accuracy": 0.0}
    curve = []
    for step in range(5, 96, 5):
        threshold = step / 100
        correct = sum((score >= threshold) == entailed for score, entailed in scores)
        accuracy = correct / max(1, len(scores))
        curve.append({"threshold": threshold, "accuracy": round(accuracy, 3)})
        if accuracy > best["accuracy"]:
            best = {"threshold": threshold, "accuracy": round(accuracy, 3)}
    return {"n": len(scores), "best": best, "curve": curve, "default_0_30": next((c["accuracy"] for c in curve if c["threshold"] == 0.3), None)}


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    for url in (args.nim_embed_url, args.nim_rerank_url):
        if url:
            assert_endpoint_allowed(url, allow_cloud=args.allow_cloud)
    config = CompanyAgentConfig.model_validate(json.loads(args.config.read_text(encoding="utf-8")))
    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    pairs = json.loads(args.entailment_golden.read_text(encoding="utf-8"))["items"]
    store = GraphStore(host=args.falkordb_host, port=args.falkordb_port)

    embedders: dict[str, Any] = {}
    if args.ollama_url:
        embedders["bge-m3"] = OllamaEmbeddingClient(base_url=args.ollama_url, model=args.ollama_model, dimension=1024)
    if args.nim_embed_url:
        embedders["nim-embed"] = NimEmbeddingClient(base_url=args.nim_embed_url, model=args.nim_embed_model, dimension=args.nim_embed_dimension, api_key=args.api_key)
    rerankers: dict[str, Any] = {"none": NullReranker()}
    if args.local_reranker:
        rerankers["local"] = CrossEncoderReranker(model=args.local_reranker)
    if args.nim_rerank_url:
        rerankers["nim-rerank"] = NimReranker(base_url=args.nim_rerank_url, model=args.nim_rerank_model, api_key=args.api_key)
    if not embedders:
        sys.exit("pass --ollama-url and/or --nim-embed-url")

    configurations: list[dict[str, Any]] = []
    for embed_name, embeddings in embedders.items():
        tenant, version = uuid4(), uuid4()
        before = _memory(store)
        index_latency: list[float] = []
        with timed(index_latency):
            report = await KnowledgeIndexer(store, embeddings).index_version(tenant_id=tenant, agent_version_id=version, config=config, rebuild=True)
        after = _memory(store)
        try:
            for rerank_name, reranker in rerankers.items():
                retriever = FalkorGraphFactRetriever(store, embeddings, None if rerank_name == "none" else reranker)
                metrics = await _golden_metrics(retriever, tenant, version, golden, args.k)
                passed = metrics[f"recall_at_{args.k}"] >= BASELINE["recall_at_3_min"] and metrics["mrr"] >= BASELINE["mrr_min"]
                configurations.append({"embedding": embed_name, "profile": f"{embeddings.model_name}#{embeddings.dimension}", "reranker": rerank_name, **metrics, "index_ms": round(index_latency[0], 1), "index_facts": report.fact_count, "falkordb_memory_delta_bytes": (after - before) if before is not None and after is not None else None, "meets_baseline": passed})
        finally:
            await store.delete_graph(report.graph_name)

    calibration = {name: await _entailment_calibration(reranker, pairs) for name, reranker in rerankers.items() if name != "none"}
    return {"config": str(args.config), "golden_items": len(golden), "baseline": BASELINE, "configurations": configurations, "entailment_calibration": calibration, "recommendation": _recommend(configurations)}


def _recommend(configurations: list[dict[str, Any]]) -> str:
    winners = [c for c in configurations if c["meets_baseline"]]
    if not winners:
        return "Test edilen yapılandırmalar ADR-002 eşiğini karşılamadı; başarısız golden soruları inceleyin. Bu rapor üretim ayarlarını değiştirmez."
    best = max(winners, key=lambda c: (c["mrr"], -c["latency_ms"]["p95"]))
    return f"{best['embedding']} + {best['reranker']} (MRR {best['mrr']}, p95 {best['latency_ms']['p95']} ms) baseline'ı karşılıyor."


def main() -> None:
    args = _parse_args()
    report = asyncio.run(_run(args))
    write_report(args.report, report)
    print(json.dumps({k: report[k] for k in ("configurations", "recommendation")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
