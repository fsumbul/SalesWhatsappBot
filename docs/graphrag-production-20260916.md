# GraphRAG production rollout — 2026-09-16

Furkan's `8ee9fb8` architecture is deployed to Ashiraai. The previous live API
matched `main` (`52e0eb0`); the unrelated natural-conversation/campaign-import
branch was not included. Deployment work lives on
`codex/graphrag-production-20260916`.

## Live state

- PostgreSQL migration: `b7c4d9e2f013` (six tenant-isolated knowledge tables).
- Artı Kasnak: version 17, copied from the actual live version 16, with all 65
  facts preserved. Only `response_mode=hybrid` and the grounded-generation
  policy (`generated_topics=[details]`) changed. The approved visible index
  contains 63 facts, 18 offerings, 22 relationships and 48 codes.
- Protected topics retain literal approved answers or explicit unsupported-data
  handling. Descriptive generation is audited and falls back to literal facts.
- Web release: `C:\sites\ashiraai\releases\graphrag-20260916-r1\web`.
  Its unchanged Next 15.0.3 dependencies use a junction to the previous release;
  retain `merged-main-20260914-r1` while this release is in use.
- Additional Python dependencies are isolated under that release's
  `dependencies` directory, activated by `ashira_knowledge.pth` in the embedded
  Python site-packages. Existing dependency files were not overwritten.
- The private production `.env` was preserved byte-for-byte. Non-secret
  selectors live in `C:\sites\ashiraai\knowledge-runtime.cmd`, called by the
  existing `llm-runtime.cmd`.

## Services and connectivity

The existing Vast Qwen endpoint (`qwen3.8-27b`, port 18080) remains unchanged.
Two additional Supervisor services run there under `/workspace/ashira-knowledge`:

- `ashira-falkordb`: loopback 6381, AOF `everysec` plus periodic snapshots.
  Native Redis/FalkorDB binaries and bundled shared libraries came from the
  pinned `falkordblite==0.10.0` distribution; Redis reports 8.6.2.
- `ashira-embeddings`: Ollama 0.34.1 on loopback 11439, BGE-M3, 1024 dimensions.
  CPU execution keeps the existing nearly-full Qwen GPU allocation intact.

`AshiraaiKnowledgeTunnel` forwards only loopback 6381 and 11439 over the existing
restricted Windows-to-Vast SSH identity. Its authorized-key restrictions still
prohibit shell execution; only the required loopback destinations were added.
The existing model tunnel is independent.

`AshiraaiKnowledgeWorker` consumes only `knowledge`; the customer worker still
consumes only `agent_runtime`. Slow document processing does not occupy the
customer worker process. Both still share the same model endpoint.

Cross-encoder reranking is disabled on this deployment. Graph, exact-code,
full-text and vector retrieval with reciprocal-rank fusion are active.
The source upload, website ingestion, candidate review, revocation and media
routes are deployed. IIS's existing API catch-all already covers `/media/k/*`.

FalkorDB data and models survive ordinary instance restart, but container
recycle/destruction is a separate durability boundary. PostgreSQL remains the
source of truth for approved knowledge and uploaded documents. Preserve the
FalkorDB data directory before recycling if conversation memory is needed;
rebuild approved indexes with `knowledge_index.py` after infrastructure recovery.

## Verification

- 211 knowledge, generation, company, WhatsApp and runtime tests passed against
  a disposable PostgreSQL with the restricted app role, real FalkorDB and real
  BGE-M3. No skipped tests (`REQUIRE_DB_TESTS=1`).
- 20 production-settings, preflight and RLS tests passed.
- Added a real-Postgres regression for the indexing CLI: tenant cleanup rolls
  back and expires ORM attributes, so indexing inputs must be materialized
  before cleanup. The regression passes.
- Ruff checks passed for the new knowledge/runtime code and operational scripts.
- Web TypeScript checking and production build passed. One existing unrelated
  React hook dependency warning remains on the selection form page.
- Eight real model scenarios passed, including descriptive hybrid generation,
  deterministic navigation, dimensions, bearing code, quote and unknown price/
  stock. Rechecked the actual production config with 12 stale history entries.
  No Meta message was sent by these checks.
- Production runtime preflight passed: migration, restricted DB role, Meta,
  model, Redis, worker, task and host gates.
- Knowledge preflight passed: graph, embedding dimension, live index and a
  dedicated knowledge worker.
- Signed no-message webhook probe returned 200. Local and external `/healthz`
  and `/tr` returned 200; unauthenticated knowledge API requests were rejected.
- Real descriptive responses took about 28–34 seconds on the current Qwen
  deployment; retrieval itself was roughly 75–125 ms on the model host.

Run the checks on Windows after calling `llm-runtime.cmd`:

```bat
cd /d C:\sites\ashiraai\app
C:\sites\ashiraai\runtime\python312-embed\python.exe scripts\runtime_preflight.py --tenant-slug kasnak --require-llm
C:\sites\ashiraai\runtime\python312-embed\python.exe scripts\knowledge_preflight.py --tenant-slug kasnak
C:\sites\ashiraai\runtime\python312-embed\python.exe scripts\knowledge_index.py --tenant-slug kasnak
```

A fresh user-sent WhatsApp message is still required to confirm end-to-end
customer delivery after rollout.

## Recovery

Backup directory: `C:\sites\ashiraai\backups\graphrag-20260916-r1`. It contains
API files, the server-local private environment, launchers, task states and a
custom PostgreSQL dump. The deployment package and scripts remain in
`C:\sites\ashiraai\staging\graphrag-20260916` for audit.

For a retrieval outage, set `KNOWLEDGE_BACKEND=lexical` in the non-secret runtime
selector and restart the API, agent worker, knowledge worker and recovery tasks.
For a generation issue, set `HYBRID_GENERATION_ENABLED=false` there and restart
those tasks. Both are narrower recovery paths than reverting the database.

Version 16 remains archived and its graph remains available. Restore response
policy using `AgentService.rollback_to` (creates an audited new version), not
by editing existing version rows. The schema migration is additive; do not
blindly downgrade/drop populated knowledge tables when rolling application
files back. Preserve new uploaded data and plan compatibility first.
