"""Timing must preserve concurrency, cancellation and the business outcome."""
import asyncio

import pytest

from src.core.runtime_timing import (
    MAX_SPANS,
    RuntimeTiming,
    current_timing,
    timed_stage,
    timing_context,
    timing_span,
)


async def test_parallel_spans_have_task_local_parents_and_no_payloads():
    trace = RuntimeTiming("job")

    @timed_stage("child")
    async def child(secret):
        await asyncio.sleep(0)
        return secret

    with timing_context(trace), timing_span("root"):
        assert await asyncio.gather(child("private-one"), child("private-two")) == [
            "private-one", "private-two",
        ]
    report = trace.snapshot()
    assert [s["parent_id"] for s in report["spans"]] == [None, 0, 0]
    assert all(s["status"] == "ok" for s in report["spans"])
    assert "private" not in str(report)
    assert current_timing() is None


async def test_error_and_cancellation_record_type_but_propagate():
    trace = RuntimeTiming("job")
    with timing_context(trace):
        with pytest.raises(ValueError), timing_span("invalid"):
            raise ValueError("secret response")
        with pytest.raises(asyncio.CancelledError), timing_span("cancelled"):
            raise asyncio.CancelledError()
    assert [s["status"] for s in trace.spans] == ["error", "cancelled"]
    assert trace.spans[0]["error_type"] == "ValueError"
    assert "secret" not in str(trace.snapshot())
    assert all("duration_ms" in s for s in trace.spans)


def test_bounded_spans_and_disabled_context():
    trace = RuntimeTiming("job")
    with timing_context(trace):
        for _ in range(MAX_SPANS + 10):
            with timing_span("step"):
                pass
    assert len(trace.spans) == MAX_SPANS
    assert trace.dropped == 10
    with timing_context(None), timing_span("disabled"):
        assert current_timing() is None


def test_logging_failure_does_not_change_operation(monkeypatch):
    from src.core import runtime_timing
    def fail(*args, **kwargs):
        raise OSError("logging unavailable")
    monkeypatch.setattr(runtime_timing.logger, "info", fail)
    with timing_context(RuntimeTiming("job")), timing_span("send"):
        result = 42
    assert result == 42


async def test_timing_persistence_failure_cannot_retry_successful_send(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from src.workers import agent_runtime

    sends = 0

    async def successful_operation(*args):
        nonlocal sends
        sends += 1
        current_timing().claimed = True
        return {"status": "sent"}

    @asynccontextmanager
    async def unavailable_database(*args):
        raise OSError("telemetry database unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(agent_runtime, "get_settings", lambda: SimpleNamespace(runtime_timing_enabled=True))
    monkeypatch.setattr(agent_runtime, "_process_runtime_job_instrumented", successful_operation)
    monkeypatch.setattr(agent_runtime, "session_scope", unavailable_database)
    assert await agent_runtime._process_runtime_job(uuid4(), uuid4()) == {"status": "sent"}
    assert sends == 1
    assert current_timing() is None
