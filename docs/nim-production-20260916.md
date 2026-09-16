# NIM integration code rollout — 2026-09-16

Merged Furkan's `7709bd0` from `claude/quizzical-agnesi-e57419` into
`codex/graphrag-production-20260916` and deployed both the API/bot and web.
The prior GraphRAG production fixes remain, including materializing index
inputs before the tenant-context rollback. The incoming developer-specific
`apps/api/.venv` symlink was excluded.

## Active production behavior

All five LLM roles (customer, generation, extraction, memory, admin) still
resolve to `qwen3.8-27b`. Embeddings remain Ollama BGE-M3, 1024 dimensions,
on the existing CPU service. Live Artı Kasnak version 17 remains hybrid with
all 65 facts preserved, including 63 customer-visible indexed facts.

This deploy adds role-based LLM clients, optional NIM embedding/reranking
adapters, guardrail integration, OCR/layout ingestion, visual verification
and the corresponding knowledge panel changes. It does **not** provision
NIM model servers or enable guardrail, OCR or vision flags. Those flags
remain false; separate real NIM endpoint acceptance is required before
activation. Existing generation and retrieval services remain active.
There is no claimed latency improvement from this rollout.

## Release and schema

- Schema head: `d9e6f1a2b035`, following `c8d5e0f1a024`; additive chunk guard
  and media verification fields.
- Web: `C:\sites\ashiraai\releases\nim-20260916-r1\web`.
- API: `C:\sites\ashiraai\app`.
- New dependency: `pypdfium2==5.13.0`, isolated in the new release's
  `dependencies` and loaded by `ashira_nim.pth`. Retain the previous
  `ashira_knowledge.pth` and GraphRAG dependency release.
- The web dependency junction still uses `merged-main-20260914-r1`; retain
  that release too. The web binds `localhost:3101` (IPv6 loopback).
- Rebuilt the approved-fact graph and document-chunk graph for the new
  `bge-m3#1024` embedding identity. The document graph had zero chunks.
- Deployment package SHA-256:
  `1800535fa44e458dc91e73dcbe265d8a7a0890801b9bde6be33bd6924561691d`.

## Verification

- 403 focused API tests passed, no skips, with `REQUIRE_DB_TESTS=1`, a
  disposable migrated PostgreSQL database, restricted application role,
  FalkorDB and BGE-M3.
- Ruff passed on changed application/script/test Python files.
- Web TypeScript check and production build passed. One pre-existing React
  hook dependency warning remains on the selection form.
- Real production-model checks passed using the actual live company facts
  and a separate temporary graph: deterministic product navigation with stale
  history, generated casting-material explanation, and unknown price/stock
  handoff. The temporary graph was removed. No customer messages were sent.
- Production runtime preflight passed, including schema, restricted DB role,
  Meta bindings, Qwen, Redis, workers and scheduled tasks.
- Knowledge preflight passed: graph, embedding, live index and knowledge worker.
- Signed no-message public webhook probe returned 200.
- Local API `/healthz`, local web `/tr`, public `/healthz` and public `/tr`
  returned 200. Unauthenticated knowledge access returned 401.
- All deployed API file hashes matched the package. Production `.env` hash
  matched the pre-deployment backup. Live version/fact count and all five
  role model settings were rechecked after deployment.

A fresh user-sent WhatsApp message is still needed to confirm customer delivery
after this rollout; the checks above do not send messages.

## Recovery and retained artifacts

Backup: `C:\sites\ashiraai\backups\nim-20260916-r1`, including the server-local
private environment, application files, launchers, task state and database dump
`db-pre-agent-runtime-20260916-081102.dump`. Staging scripts and manifest remain
in `C:\sites\ashiraai\staging\nim-20260916`.

Workers were drained before replacement. API, agent worker, recovery,
knowledge worker, web and both persistent SSH tunnels are running. The
one-time package-transfer HTTP server and additional SSH forwarding permission
were removed after transfer.

Prefer forward repair for any issue after migration. If restoring old code,
review additive-schema compatibility and preserve newly written guard/media
data; do not blindly downgrade the database. Restore the previous web launcher
from backup to switch web release independently. The existing GraphRAG rollout
document describes narrower retrieval/generation fallback controls.
