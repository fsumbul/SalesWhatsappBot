#!/usr/bin/env python3
"""WP2 — OCR / layout chain evaluation on scanned Turkish pages (synthetic fixtures).

Reads ``experiments/nim/fixtures/ocr/<name>.(png|jpg|pdf)`` with ground truth
``<name>.txt`` (and optional ``<name>.table.json``), runs the same pipeline the
ingest worker uses (``build_document_ocr`` from the application Settings) and
reports CER / WER, table cell accuracy and per-page latency. Run from
``apps/api`` with ``KNOWLEDGE_OCR_ENABLED=true`` and the base URLs set::

    poetry run python ../../experiments/nim/ocr_eval.py --report ../../experiments/nim/ocr-report.json
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

from _common import add_common_args, assert_endpoint_allowed, latency_summary, timed, write_report  # noqa: E402
from src.core.config import get_settings  # noqa: E402
from src.modules.knowledge.chunking import normalize_whitespace  # noqa: E402
from src.modules.knowledge.ocr import build_document_ocr, pdf_page_count, regions_to_units, render_pdf_page  # noqa: E402

THRESHOLDS = {"cer_max": 0.05, "table_cell_accuracy_min": 0.90}


def levenshtein(a: list[str] | str, b: list[str] | str) -> int:
    previous = list(range(len(b) + 1))
    for i, item in enumerate(a, start=1):
        current = [i]
        for j, other in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (item != other)))
        previous = current
    return previous[-1]


def _rate(hyp: str, ref: str, *, words: bool) -> float:
    h = hyp.split() if words else list(hyp)
    r = ref.split() if words else list(ref)
    return levenshtein(h, r) / max(1, len(r))


def _cell_accuracy(expected: list[list[str]], got: tuple[tuple[str, ...], ...] | None) -> float | None:
    if not expected:
        return None
    total = sum(len(row) for row in expected)
    hits = 0
    for r, row in enumerate(expected):
        for c, cell in enumerate(row):
            try:
                hits += normalize_whitespace(str(got[r][c])) == normalize_whitespace(cell) if got else False
            except IndexError:
                pass
    return hits / max(1, total)


async def _evaluate(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    for endpoint in settings.nim_endpoints():
        assert_endpoint_allowed(endpoint.url, allow_cloud=args.allow_cloud)
    pipeline = build_document_ocr(settings)
    if pipeline is None:
        sys.exit("KNOWLEDGE_OCR_ENABLED=true and KNOWLEDGE_OCR_BASE_URL are required")
    samples: list[dict[str, Any]] = []
    latency: list[float] = []
    for truth in sorted(args.fixtures.glob("*.txt")):
        stem = truth.with_suffix("")
        images: list[tuple[str, bytes]] = []
        for ext in (".png", ".jpg", ".jpeg"):
            if stem.with_suffix(ext).exists():
                images.append((stem.name, stem.with_suffix(ext).read_bytes()))
        if stem.with_suffix(".pdf").exists():
            data = stem.with_suffix(".pdf").read_bytes()
            for page in range(1, pdf_page_count(data) + 1):
                jpeg, _w, _h = render_pdf_page(data, page, dpi=settings.knowledge_ocr_dpi)
                images.append((f"{stem.name}#page={page}", jpeg))
        expected_text = normalize_whitespace(truth.read_text(encoding="utf-8"))
        table_path = stem.with_suffix(".table.json")
        expected_table = json.loads(table_path.read_text(encoding="utf-8")) if table_path.exists() else []
        for name, image in images:
            with timed(latency):
                regions = await pipeline.read_page(image, "image/jpeg")
            units = regions_to_units(name, 1, regions)
            hypothesis = normalize_whitespace("\n".join(u.text for u in units if u.meta.get("kind") == "text"))
            table = next((r.cells for r in regions if r.kind == "table" and r.cells), None)
            samples.append(
                {
                    "sample": name,
                    "cer": round(_rate(hypothesis, expected_text, words=False), 4),
                    "wer": round(_rate(hypothesis, expected_text, words=True), 4),
                    "table_cell_accuracy": _cell_accuracy(expected_table, table),
                    "regions": len(regions),
                    "latency_ms": round(latency[-1], 1),
                }
            )
    if not samples:
        sys.exit(f"no fixtures found under {args.fixtures} (see README.md there)")
    cer = sum(s["cer"] for s in samples) / len(samples)
    tables = [s["table_cell_accuracy"] for s in samples if s["table_cell_accuracy"] is not None]
    cell_accuracy = sum(tables) / len(tables) if tables else None
    acceptance = {
        "cer": {"value": round(cer, 4), "max": THRESHOLDS["cer_max"], "pass": cer <= THRESHOLDS["cer_max"]},
        "table_cell_accuracy": {
            "value": None if cell_accuracy is None else round(cell_accuracy, 4),
            "min": THRESHOLDS["table_cell_accuracy_min"],
            "pass": cell_accuracy is None or cell_accuracy >= THRESHOLDS["table_cell_accuracy_min"],
        },
    }
    return {
        "pipeline": pipeline.describe,
        "dpi": settings.knowledge_ocr_dpi,
        "samples": samples,
        "latency_ms": latency_summary(latency),
        "acceptance": acceptance,
        "passed": all(v["pass"] for v in acceptance.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=_HERE / "fixtures" / "ocr")
    add_common_args(parser)
    args = parser.parse_args()
    report = asyncio.run(_evaluate(args))
    write_report(args.report, report)
    print(json.dumps({k: report[k] for k in ("pipeline", "latency_ms", "acceptance", "passed")}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
