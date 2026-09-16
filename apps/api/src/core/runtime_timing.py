"""Bounded, payload-free per-turn timing; nested durations are inclusive.

ContextVar parents keep concurrent retrieval branches separate. Instrumentation
never retries business operations and never records arguments or exception text.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from time import perf_counter
from typing import Any, ParamSpec, TypeVar
from uuid import uuid4

import structlog

logger = structlog.get_logger(__name__)
P = ParamSpec("P")
T = TypeVar("T")
_current: ContextVar[RuntimeTiming | None] = ContextVar("runtime_timing", default=None)
_parent: ContextVar[int | None] = ContextVar("runtime_timing_parent", default=None)
MAX_SPANS = 256


def timing_event(event: str, **fields: Any) -> None:
    # Observability must not affect the send boundary.
    with suppress(Exception):
        logger.info(event, **fields)


class RuntimeTiming:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.trace_id = str(uuid4())
        self.started_at = datetime.now(UTC)
        self.started = perf_counter()
        self.spans: list[dict[str, Any]] = []
        self.flags: dict[str, Any] = {}
        self.dropped = 0
        self.claimed = False
        self.queue_ms: float | None = None
        self.inbound_to_worker_ms: float | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "trace_id": self.trace_id,
            "started_at": self.started_at.isoformat(),
            "worker_ms": round((perf_counter() - self.started) * 1000, 2),
            "queue_ms": self.queue_ms,
            "inbound_to_worker_ms": self.inbound_to_worker_ms,
            "flags": dict(self.flags),
            "spans": [dict(s) for s in self.spans],
            "dropped_spans": self.dropped,
        }


@contextmanager
def timing_context(timing: RuntimeTiming | None) -> Iterator[None]:
    token = _current.set(timing)
    parent_token = _parent.set(None)
    try:
        yield
    finally:
        _parent.reset(parent_token)
        _current.reset(token)


def timing_flags(**values: Any) -> None:
    timing = _current.get()
    if timing is not None:
        timing.flags.update(values)


def current_timing() -> RuntimeTiming | None:
    return _current.get()


@contextmanager
def timing_span(name: str) -> Iterator[None]:
    timing = _current.get()
    if timing is None:
        yield
        return
    if len(timing.spans) >= MAX_SPANS:
        timing.dropped += 1
        yield
        return
    started = perf_counter()
    span = {
        "id": len(timing.spans), "parent_id": _parent.get(), "stage": name,
        "start_ms": round((started - timing.started) * 1000, 2), "status": "running",
    }
    timing.spans.append(span)
    token = _parent.set(span["id"])
    fields = {"job_id": timing.job_id, "trace_id": timing.trace_id,
              "stage": name, "span_id": span["id"], "parent_id": span["parent_id"]}
    timing_event("runtime.stage.started", **fields)
    try:
        yield
    except BaseException as exc:
        span["status"] = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
        span["error_type"] = type(exc).__name__
        raise
    else:
        span["status"] = "ok"
    finally:
        span["duration_ms"] = round((perf_counter() - started) * 1000, 2)
        _parent.reset(token)
        timing_event("runtime.stage.finished", **fields, status=span["status"],
             duration_ms=span["duration_ms"], error_type=span.get("error_type"))


async def timed_await(name: str, operation: Awaitable[T]) -> T:
    with timing_span(name):
        return await operation


def timed_stage(name: str) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    def decorate(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(fn)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            with timing_span(name):
                return await fn(*args, **kwargs)
        return wrapped
    return decorate
