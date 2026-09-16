# ruff: noqa: E402, RUF001
"""Query the GraphRAG index directly and evaluate a golden question set.

Prints ranked candidates with channel provenance and per-stage latency, so
retrieval quality can be judged without running WhatsApp. Run from ``apps/api``::

    poetry run python scripts/knowledge_search.py --tenant-id ... --version-id ... "6211 rulman hangi mile uygun?"
    poetry run python scripts/knowledge_search.py --tenant-id ... --version-id ... --golden config/knowledge_golden.arti_kasnak.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.modules.knowledge.ports import RetrievalRequest
from src.modules.knowledge.service import build_fact_retriever


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?")
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--version-id", type=UUID, required=True)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--anchor", action="append", default=[], help="trusted subject id")
    parser.add_argument("--golden", type=Path, help="JSON list of {question, expected_fact_ids}")
    return parser.parse_args()


async def _async_main(args: argparse.Namespace) -> None:
    retriever = build_fact_retriever()
    if retriever is None:
        raise SystemExit("KNOWLEDGE_BACKEND=falkordb and EMBEDDING_PROVIDER=ollama|nim are required")

    async def ask(question: str, anchors: tuple[str, ...] = ()) -> list[str]:
        result = await retriever.retrieve(
            RetrievalRequest(
                tenant_id=args.tenant_id,
                agent_version_id=args.version_id,
                query=question,
                anchor_subject_ids=anchors,
                max_candidates=args.k,
            )
        )
        timings = {k: round(v) for k, v in result.timings_ms.items()}
        print(f"\nQ: {question}")
        print(f"   codes={list(result.query_codes)} reranked={result.reranked} degraded={result.degraded} ms={timings}")
        for candidate in result.candidates:
            flag = "*" if candidate.exact_code else " "
            print(f"   {candidate.score:6.3f} {flag} {candidate.fact_id:42s} {','.join(candidate.channels)}")
        return list(result.fact_ids)

    if args.golden is not None:
        items = json.loads(args.golden.read_text(encoding="utf-8"))
        hits_at_1 = hits_at_k = 0
        reciprocal = 0.0
        for item in items:
            ranked = await ask(item["question"], tuple(item.get("anchor_subject_ids", [])))
            expected = set(item["expected_fact_ids"])
            first = next((i for i, fact_id in enumerate(ranked) if fact_id in expected), None)
            if first is not None:
                hits_at_k += 1
                reciprocal += 1 / (first + 1)
                hits_at_1 += first == 0
            print(f"   -> expected {sorted(expected)}: {'HIT@' + str(first + 1) if first is not None else 'MISS'}")
        n = len(items)
        print(
            f"\nGolden set: n={n} recall@1={hits_at_1 / n:.2f} recall@{args.k}={hits_at_k / n:.2f} "
            f"MRR={reciprocal / n:.2f}"
        )
        return
    if not args.question:
        raise SystemExit("pass a question or --golden")
    await ask(args.question, tuple(args.anchor))


if __name__ == "__main__":
    asyncio.run(_async_main(_parse_args()))
