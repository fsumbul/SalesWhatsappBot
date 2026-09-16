#!/usr/bin/env python3
"""WP5 — role-based LLM evaluation: local Qwen3 vs a Nemotron NIM for background roles.

(a) extraction: every fixture document is chunked and passed through the real
``extract_chunk`` pipeline (schema-constrained call + server-side validation)
with both models; reports raw proposals, validated facts, the validated ratio,
the share of Turkish-looking outputs and latency.
(b) memory: ``extract_memory`` on synthetic dialogues; reports subject-set and
intent accuracy (enum-only output, language-insensitive).

Run from ``apps/api``::

    poetry run python ../../experiments/nim/role_llm_eval.py \\
      --baseline-url http://127.0.0.1:11434 --baseline-model qwen3:8b \\
      --candidate-url http://gpu:8050/v1 --candidate-model nvidia/nemotron-3.5-lightning-30b-a3b \\
      --report ../../experiments/nim/role-llm-report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_API = _HERE.parents[1] / "apps" / "api"
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_API))

from _common import add_common_args, assert_endpoint_allowed, latency_summary, timed, write_report  # noqa: E402
from src.integrations.llm import ChatCompletionsLLMClient, LLMClient, LLMMessage, OllamaLLMClient  # noqa: E402
from src.modules.agents.company_config import CompanyAgentConfig  # noqa: E402
from src.modules.knowledge.chunking import chunk_text  # noqa: E402
from src.modules.knowledge.extraction import ChunkExtraction, _context, _SYSTEM, extraction_schema, validate_extraction  # noqa: E402
from src.modules.knowledge.memory import extract_memory  # noqa: E402

THRESHOLDS = {"turkish_ratio_min": 0.98}
_TURKISH_CHARS = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
_TURKISH_WORDS = {"ve", "ile", "için", "bir", "olarak", "da", "de", "bu", "veya", "üretilir", "mm"}


def looks_turkish(text: str) -> bool:
    """Cheap heuristic: Turkish letters or at least two Turkish function words."""

    if _TURKISH_CHARS.search(text):
        return True
    words = set(re.findall(r"\w+", text.lower()))
    return len(words & _TURKISH_WORDS) >= 2


def _client(provider: str, url: str, model: str, schema_mode: str, api_key: str) -> LLMClient:
    if provider == "ollama":
        return OllamaLLMClient(base_url=url, model=model, num_ctx=8192)
    return ChatCompletionsLLMClient(base_url=url, model=model, api_key=api_key, schema_mode=schema_mode)  # type: ignore[arg-type]


async def _extraction(llm: LLMClient, config: CompanyAgentConfig, docs: list[Path]) -> dict[str, Any]:
    raw_total = validated_total = turkish = 0
    latency: list[float] = []
    chunks = 0
    for doc in docs:
        for piece in chunk_text(doc.read_text(encoding="utf-8")):
            chunks += 1
            with timed(latency):
                raw = await llm.complete(
                    [LLMMessage(role="user", content=f"LOCATION: {doc.name}\n\n{piece.text[:6000]}")],
                    system=_SYSTEM + json.dumps(_context(config), ensure_ascii=False),
                    max_tokens=1400,
                    response_schema=extraction_schema(config, allow_new_offerings=True),
                )
            try:
                parsed = ChunkExtraction.model_validate_json(raw)
            except ValueError:
                continue
            raw_total += len(parsed.facts)
            validated = validate_extraction(config, parsed, piece.text, allow_new_offerings=True)
            validated_total += len(validated.facts)
            turkish += sum(looks_turkish(f.customer_text) for f in validated.facts)
    return {
        "chunks": chunks,
        "raw_facts": raw_total,
        "validated_facts": validated_total,
        "validated_ratio": round(validated_total / raw_total, 3) if raw_total else 0.0,
        "turkish_ratio": round(turkish / validated_total, 3) if validated_total else 0.0,
        "latency_ms": latency_summary(latency),
    }


async def _memory(llm: LLMClient, config: CompanyAgentConfig, dialogues: list[dict[str, Any]]) -> dict[str, Any]:
    subject_hits = intent_hits = 0
    latency: list[float] = []
    rows = []
    for item in dialogues:
        turns = [LLMMessage(role=t["role"], content=t["content"]) for t in item["turns"]]
        with timed(latency):
            extraction = await extract_memory(llm, config, turns)
        subject_ok = set(extraction.subject_ids) == set(item["subject_ids"])
        intent_ok = extraction.intent == item["intent"]
        subject_hits += subject_ok
        intent_hits += intent_ok
        rows.append({"id": item["id"], "subjects": extraction.subject_ids, "intent": extraction.intent, "subject_ok": subject_ok, "intent_ok": intent_ok})
    n = max(1, len(dialogues))
    return {"n": len(dialogues), "subject_accuracy": round(subject_hits / n, 3), "intent_accuracy": round(intent_hits / n, 3), "latency_ms": latency_summary(latency), "rows": rows}


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    assert_endpoint_allowed(args.candidate_url, allow_cloud=args.allow_cloud)
    config = CompanyAgentConfig.model_validate(json.loads(args.config.read_text(encoding="utf-8")))
    docs = sorted((args.fixtures / "docs").glob("*.md"))
    dialogues = json.loads((args.fixtures / "memory_dialogues.json").read_text(encoding="utf-8"))["items"]
    models = {
        "baseline": _client(args.baseline_provider, args.baseline_url, args.baseline_model, "response_format", args.api_key),
        "candidate": _client(args.candidate_provider, args.candidate_url, args.candidate_model, args.candidate_schema_mode, args.api_key),
    }
    results: dict[str, Any] = {}
    for name, llm in models.items():
        results[name] = {"extraction": await _extraction(llm, config, docs), "memory": await _memory(llm, config, dialogues)}
    base, cand = results["baseline"], results["candidate"]
    extraction_ok = (
        cand["extraction"]["turkish_ratio"] >= THRESHOLDS["turkish_ratio_min"]
        and cand["extraction"]["validated_ratio"] >= base["extraction"]["validated_ratio"]
        and cand["extraction"]["validated_facts"] >= base["extraction"]["validated_facts"]
    )
    memory_ok = (
        cand["memory"]["subject_accuracy"] >= base["memory"]["subject_accuracy"]
        and cand["memory"]["intent_accuracy"] >= base["memory"]["intent_accuracy"]
    )
    return {
        "baseline": {"provider": args.baseline_provider, "model": args.baseline_model},
        "candidate": {"provider": args.candidate_provider, "model": args.candidate_model, "schema_mode": args.candidate_schema_mode},
        "results": results,
        "decision": {
            "extraction_role_may_move": extraction_ok,
            "memory_role_may_move": memory_ok,
            "rule": "çıkarım: Türkçe oranı ≥ 0.98 ve aday kalitesi baseline'dan düşük değil; hafıza: enum doğruluğu baseline'dan düşük değil",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=_API / "config" / "arti_kasnak.production.json")
    parser.add_argument("--fixtures", type=Path, default=_HERE / "fixtures" / "role_llm")
    parser.add_argument("--baseline-provider", default="ollama")
    parser.add_argument("--baseline-url", default="http://127.0.0.1:11434")
    parser.add_argument("--baseline-model", default="qwen3:8b")
    parser.add_argument("--candidate-provider", default="chat_compatible")
    parser.add_argument("--candidate-url", required=True)
    parser.add_argument("--candidate-model", default="nvidia/nemotron-3.5-lightning-30b-a3b")
    parser.add_argument("--candidate-schema-mode", default="nvext_guided_json", choices=["nvext_guided_json", "response_format"])
    parser.add_argument("--api-key", default="")
    add_common_args(parser)
    args = parser.parse_args()
    report = asyncio.run(_run(args))
    write_report(args.report, report)
    print(json.dumps(report["decision"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
