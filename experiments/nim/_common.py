"""Shared helpers for the NIM evaluation scripts (synthetic data only)."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PUBLIC_HOST_DENYLIST = ("integrate.api.nvidia.com", "ai.api.nvidia.com", "api.nvcf.nvidia.com")


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--report", type=Path, required=True, help="JSON report path")
    parser.add_argument(
        "--allow-cloud",
        action="store_true",
        help="permit build.nvidia.com trial hosts — SYNTHETIC fixtures only, never tenant data",
    )


def assert_endpoint_allowed(url: str, *, allow_cloud: bool) -> None:
    host = (urlparse(url).hostname or "").lower()
    denied = any(host == d or host.endswith("." + d) for d in PUBLIC_HOST_DENYLIST)
    if denied and not allow_cloud:
        sys.exit(
            f"{url} is a public trial endpoint; pass --allow-cloud only for synthetic fixtures"
        )
    if denied:
        print("WARNING: cloud endpoint in use — synthetic data only", file=sys.stderr)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100) * (len(ordered) - 1))))
    return ordered[index]


def latency_summary(values_ms: list[float]) -> dict[str, float]:
    if not values_ms:
        return {"n": 0, "p50": 0.0, "p95": 0.0, "mean": 0.0}
    return {
        "n": len(values_ms),
        "p50": round(percentile(values_ms, 50), 1),
        "p95": round(percentile(values_ms, 95), 1),
        "mean": round(statistics.fmean(values_ms), 1),
    }


@contextmanager
def timed(bucket: list[float]) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        bucket.append((time.perf_counter() - started) * 1000)


def precision_recall(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}


def write_report(path: Path, payload: dict[str, Any]) -> None:
    payload = {"generated_at": datetime.now(UTC).isoformat(), **payload}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report written: {path}")
