# WhatsApp turn timing — 2026-09-16

Production timing is enabled with `RUNTIME_TIMING_ENABLED=true` (the default).
The private production `.env` was not changed. To disable it, set the flag in
the non-secret runtime launcher and restart API/agent workers.

Every claimed inbound job now has an `audit.timing` report with a trace ID,
queue wait, worker elapsed time, execution flags and at most 256 spans. Logs
emit `runtime.stage.started`, `runtime.stage.finished` and `runtime.timing`
with the same job/trace IDs. Webhook persistence and Celery dispatch are logged
as `runtime.webhook.timing`, correlated by job ID. No prompt, reply, phone,
credential, query parameters or exception text is added to these timing logs.

## Covered response paths

The worker envelope covers normal replies, guided menus, selection flows,
clarification, handoff, decline, fallback, early skip, superseded messages,
opt-out, sender rejection, retryable failures and ambiguous external sends.
A duplicate/deferred delivery is logged without overwriting the claimed
attempt's stored timing. Every new attempt has a fresh trace ID; the job audit
keeps the latest claimed attempt and logs retain earlier attempt events.

| Stage | Meaning |
| --- | --- |
| queue_ms | Durable job creation to this worker attempt's entry; retries include previous attempts/backoff |
| inbound_to_worker_ms | Webhook handler entry to this worker attempt, when available |
| db.* | Queries, commits, refreshes, history, live config and send-lock wait |
| guardrail.* / nim.http | Optional input classification and classifier transport |
| selection.* | Applicability, selection workflow and media download |
| whatsapp.typing | Initial and refreshed typing-indicator request |
| knowledge.setup / memory.load | Retriever setup and existing customer memory |
| llm.intent | Structured interpretation of the customer's requests |
| retrieval.* | Fact, vector, full-text, exact-code, locality and document evidence searches |
| embedding.http / graph.query / reranker | Embedding, graph and reranking calls within retrieval |
| llm.evidence_decision | Model decision about which approved evidence answers each request |
| generation / generation.audit | Grounded prose generation and verification |
| llm.http | Individual model call, nested within intent, evidence or generation |
| whatsapp.send | The single external outbound request, including errors |
| handoff.queue | Internal human-review assignment |
| memory.enqueue | Scheduling later memory enrichment, not the background job's execution |

Flags identify selection/guardrail routing, enabled knowledge inputs,
response source, answer origin, fallback, generation/retrieval status, action,
transport, Meta acceptance, attempt and final result. Early exits carry static
server-authored reasons. Missing spans mean the stage did not run (or the
256-span cap was reached); `dropped_spans` explicitly reports truncation.
Optional NIM classifiers are still disabled in production.

Durations use a monotonic clock. A parent includes child time; parallel spans
overlap. **Do not sum all spans or add queue_ms to inbound_to_worker_ms.**
Use root wall time and span offsets/parent IDs to interpret the critical path.
Worker time ends before the best-effort telemetry persistence write (bounded
by two seconds). `runtime.stage.started` without a finish can help locate a
process crash; the durable pre-send snapshot is intentionally incomplete in
that case. There is no automatic resend added by this instrumentation.

`whatsapp.send` measures Meta HTTP acceptance, not delivery to the handset.
The report also lists existing delivery/read callbacks. Callback differences
are approximate because Meta timestamps have second precision and a separate
clock; negative differences are reported as unavailable.

## Read a report

After calling the usual `llm-runtime.cmd` on Windows, from the application
folder:

```bat
C:\sites\ashiraai\runtime\python312-embed\python.exe scripts\runtime_timing_report.py --tenant-slug kasnak --limit 10
```

Use `--job-id UUID` for one job. The JSON report includes
`inbound_to_meta_ack_ms`, individual spans and delivery callback delays.
It does not expose message bodies or phone numbers. Older messages have no
new timing report; use fresh messages to collect the stage breakdown.

## Validation and deployment

243 tests passed with PostgreSQL required and no skips. After adding callback
row locking to protect concurrent timing writes, all 34 timing/runtime
integration tests passed again. Tests cover parallel parentage, cancellation,
error type without error payloads, bounded collection, logging failures,
telemetry persistence failure after successful send, durable success/failure
timing and duplicate-delivery at-most-once behavior. Ruff passed on all changed
Python files.

Backup: `C:\sites\ashiraai\backups\timing-20260916-r1`, including a server-local
DB dump and environment backup. Package and verified manifest are retained at
`C:\sites\ashiraai\staging\timing-20260916`. No schema/model/frontend changes
were needed. Source baselines and deployed hashes were checked, workers were
drained and API/agent/knowledge/recovery tasks restarted. Existing web and
model services were retained. Rollback uses the backed-up source files and
removes only the new timing module/report script, followed by worker restart.

Post-deployment runtime/knowledge preflights and signed no-message webhook
passed. Local/public health and web returned 200. All deployed source hashes
and the unchanged environment hash passed verification.

Real Qwen smoke tests (no Meta send) used the actual live v17 config/index:

| Scenario | Runtime | Intent | Fact retrieval | Evidence decision | Generation total |
| --- | ---: | ---: | ---: | ---: | ---: |
| Guided menu | 1.32 ms | not run | not run | not run | not run |
| Casting material | 37.086 s | 9.020 s | 2.091 s | 12.237 s | 13.698 s |
| Unknown price/stock | 26.475 s | 12.838 s | 3.202 s | 10.421 s | 0.001 s |

The material answer's generation total includes 1.952 s of document evidence
search, 11.735 s of model generation and 4 ms of auditing. These are nested,
not additional time. All three scenarios passed without fallback. These are
single observations, not latency guarantees or end-to-end delivery timings.
