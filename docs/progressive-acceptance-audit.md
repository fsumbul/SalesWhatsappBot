# Progressive workflow completion audit

Status: **not complete**. This is an evidence index, not a release approval.
Scope sources: `company-platform-delivery-2026-09-14.md` (latest user corrections and delivery sequence), `progressive-workflow-delivery-2026-09-14.md` (persistent workflow implementation and evidence). Historical passing results must be scoped to their actual tests.

| Requirement | Current authoritative implementation / evidence location | Assessment / next evidence |
| --- | --- | --- |
| Simple default chat, one composer, on-demand capacity, mobile containment | `ops-chat.tsx`; `progressive_capacity.py`, `progressive_workflows.py` | Current browser evidence; capacity rendering uses fixtures, not Meta |
| Shared cards, fields, steps, errors, review, output and collapsed details | `workflow-card.tsx`, `workflow-records.tsx`, `workflow-details.tsx`; 17-state dev gallery | Implemented; limited keyboard/required/error semantics verified, not comprehensive accessibility conformance |
| Private tenant/user/session durable state; one foreground; revisions and receipts | `workflow_models.py`, migrations f34/f45/f56; `test_progressive_workflows.py` | Existing DB suite evidence; final full suite still required after final changes |
| Save before switch, pause/resume, reload, conflicts and retry identity | `workflow-card.tsx`, `ops-chat.tsx`; progressive workflows/autosave tests | Current guided browser and earlier backend evidence |
| Contact identity, duplicate reporting without merge/consent | workflows contact service; `progressive_duplicates.py` | Prior browser and DB evidence; no implied opt-in |
| Company provisioning, invite acceptance, first owner session | `progressive_provisioning.py` | 12 current real browser/API/DB checks; found and fixed unnecessary required company code on invite form |
| Owner reinvitation, team role changes, last owner protection | workflow owners/members; progressive owner invite/records and backend tests | Prior real browser/DB evidence; owner-invite model route now passed focused and full 25-case acceptance |
| Config JSON/CSV mapping, review, draft persistence, publish, version testing | workflow agents; progressive agents + live model scripts | Guided and earlier real-model evidence; 25-case deployed Windows model acceptance and live API builder/test-history reruns passed |
| Rollback creates new LIVE revision and protects drift/history | workflow agents rollback; progressive rollback + DB tests | Prior real browser, DB and focused model evidence |
| Inbox read, manual reply, delivery ambiguity, bot resume | workflow inbox; progressive inbox + backend tests | Prior boundary/browser evidence; does not prove live Meta delivery |
| Requests/quotes, notes/status/assignment, file downloads | workflow requests/records; progressive requests + backend tests | Prior synthetic DB/browser evidence; quotes are confirmed technical requests, not priced quotes |
| Outreach template/variables/consent, durable queue, cancellation/delivery | workflow outreach + canonical worker; progressive outreach/natural outreach + backend tests | Prior synthetic/boundary evidence; no live send claim |
| Real model interprets supported writes without invented fields/roles/IDs | planner/workflow intents; verify_company_models and verify_progressive_live_model | Current 25-case actual Qwen rerun passed, including owner invite; current live API builder/draft/history and real-model mobile browser reruns passed (delivery turn 26) |
| Actual company model answers isolate sectors and handle unknown price/stock | verify_company_models.py | Final deployed Windows 25-case model acceptance passed, including sector isolation and unknown price/stock boundaries |
| Secure auth, concurrent refresh, CSRF, logout, role menu | progressive_session.py | 9 current real production-web checks; rerun passed after invite fix |
| Legacy history compatibility | historical cards and backend legacy tests | Separate compatibility behavior; old browser scripts are not current acceptance. Audit remaining historical render coverage explicitly |
| New release package, manifest, fresh backups, deployment, migrations/runtime binding/IIS | package_company_release.py and original delivery sequence | Fresh r1 candidate built and all 382 hashes verified (delivery turn 26); production backup, drift check, deployment and r2 deployed-hash comparison now passed (delivery turn 28). Old ZIP/staging forbidden |
| Production preflight/model/worker/HTTPS and fresh WhatsApp round trip | original delivery sequence steps 10–11 | Production preflight, HTTPS, LIVE v15, worker and deployed 25-case Qwen acceptance passed. Fresh user message at 14:21:15 UTC processed by Qwen on v15, one attempt, no fallback; Meta read receipt verified (`/tmp/progressive-fresh-delivery.txt`) |

Next: finish remaining historical render coverage decision and end-to-end model acceptance; run the complete final requirement/model matrix and relevant backend/browser gates; only then prepare a fresh deployable package and execute the authorized delivery sequence. Keep the existing goal active until every required final gate is evidenced.
