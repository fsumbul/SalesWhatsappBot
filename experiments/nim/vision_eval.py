#!/usr/bin/env python3
"""WP3 — vision verification evaluation on labelled product images (synthetic fixtures).

Runs the same verifier the ingest worker uses (``build_media_verifier`` from
the application Settings) on ``fixtures/vision/labels.json`` and reports
subject-match precision/recall, the false-reject rate of real product photos
and latency. Run from ``apps/api`` with ``KNOWLEDGE_VISION_ENABLED=true``::

    poetry run python ../../experiments/nim/vision_eval.py --report ../../experiments/nim/vision-report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1] / "apps" / "api"))

from _common import add_common_args, assert_endpoint_allowed, latency_summary, precision_recall, timed, write_report  # noqa: E402
from src.core.config import get_settings  # noqa: E402
from src.modules.knowledge.vision import build_media_verifier, downscale_for_model  # noqa: E402

THRESHOLDS = {"precision_min": 0.90, "false_reject_max": 0.10}


async def _evaluate(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    for endpoint in settings.nim_endpoints():
        assert_endpoint_allowed(endpoint.url, allow_cloud=args.allow_cloud)
    verifier = build_media_verifier(settings)
    if verifier is None:
        sys.exit("KNOWLEDGE_VISION_ENABLED=true and KNOWLEDGE_VISION_BASE_URL are required")
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    subject_labels: dict[str, str] = labels["subject_labels"]
    results: list[dict[str, Any]] = []
    latency: list[float] = []
    tp = fp = fn = 0
    product_total = product_rejected = 0
    for item in labels["items"]:
        image = downscale_for_model((args.labels.parent / item["file"]).read_bytes(), max_edge=settings.knowledge_vision_max_edge)
        with timed(latency):
            verdict = await verifier.verify(image, "image/jpeg", subject_labels=subject_labels, context=item.get("context", ""))
        expected = item.get("expected_subject")
        got = verdict.subject_id if verdict.confidence >= settings.knowledge_vision_min_confidence else None
        if expected is not None:
            product_total += 1
            product_rejected += not verdict.is_product_photo
            tp += got == expected
            fn += got != expected
        elif got is not None:
            fp += 1
        results.append({"file": item["file"], "expected": expected, "got": got, "confidence": verdict.confidence, "is_product_photo": verdict.is_product_photo, "expected_product": bool(item.get("is_product_photo", expected is not None)), "flags": {"text_overlay": verdict.contains_text_overlay}, "latency_ms": round(latency[-1], 1)})
    match = precision_recall(tp, fp, fn)
    false_reject = product_rejected / product_total if product_total else 0.0
    acceptance = {
        "subject_precision": {"value": match["precision"], "min": THRESHOLDS["precision_min"], "pass": match["precision"] >= THRESHOLDS["precision_min"]},
        "false_reject_rate": {"value": round(false_reject, 4), "max": THRESHOLDS["false_reject_max"], "pass": false_reject <= THRESHOLDS["false_reject_max"]},
    }
    return {"model": verifier.model, "items": len(results), "subject_match": match, "latency_ms": latency_summary(latency), "acceptance": acceptance, "passed": all(v["pass"] for v in acceptance.values()), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=_HERE / "fixtures" / "vision" / "labels.json")
    add_common_args(parser)
    args = parser.parse_args()
    report = asyncio.run(_evaluate(args))
    write_report(args.report, report)
    print(json.dumps({k: report[k] for k in ("model", "items", "subject_match", "acceptance", "passed")}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
