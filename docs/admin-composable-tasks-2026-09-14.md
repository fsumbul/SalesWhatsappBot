# Composable administrative tasks

Natural read and multi-part requests can now search, follow tenant-owned references,
read records, combine evidence and prepare existing review workflows within one chat turn.
Conversation history no longer depends on the existence of a technical request.

## Execution contract

- `planner.py` extracts up to eight goals from literal excerpts of the operator's
  current message. Explicit UI actions and existing workflow confirmations retain
  their established path.
- `task_schema.py` defines the closed tool protocol; `task_tools.py` adapts the
  existing inbox, record, analytics, provider and workflow services. There is no
  model-authored SQL, route, tenant, permission or executable code.
- `task_runner.py` observes results before choosing the next step. Server-issued
  references connect searches to conversations, requests and message delivery.
  Read evidence can support multiple goals. Preparation authority stays bound to
  the original write goal.
- Completion first checks goal coverage, evidence existence, appropriate tool
  types and page coverage. Preparation status is rendered directly from server
  state. A separate model call checks the proposed read answers and remaining
  operator request against evidence; server-handled preparation clauses are
  excluded from that semantic check. Model verification is a
  semantic check, not a proof; tenant checks and write restrictions are enforced
  by code independently.
- For short transcripts whose quoted output fits the answer budget, the server
  adds any literal message excerpts omitted by the synopsis. A concise model
  synopsis therefore cannot hide a separate customer topic in those transcripts.
- A write preparation is planned again using only the original operator excerpt,
  without retrieved customer messages. It may open a canonical review, but cannot
  complete it, send, publish or apply a business change. Existing explicit
  confirmations and their revision/idempotency rules remain unchanged.
- Twelve steps, 240 seconds, bounded model inputs and duplicate-call detection
  bound work. Partial pages, omitted evidence, unresolved identity, denied access,
  provider failures and unsupported goals cannot silently become full success.
  The web proxy allows 300 seconds for chat turns, covering the initial planner
  and the bounded executor.
- A transient inference failure may retry the same model input once within the
  total deadline. This does not re-run a capability, prepare another workflow or
  repeat an external send. An HTTP/database test exercises failure after preparation
  and asserts that exactly one review remains.
- The existing session lock and client message ID make the turn, read cards,
  prepared workflows and audit durable together. Retrying a recorded turn returns
  the same response without another model call or workflow creation.

The chat renders per-goal outcomes and expandable source records. Final conversation
and record views use the existing cards, including their paging and action controls.
Failed verification retains the useful records and any prepared review, while
explicitly reporting an incomplete result.

## Scope

Adapters currently cover inbox, contacts, requests, quotes, agents, company knowledge,
versions, team, companies, conversation messages, request details/files, delivery,
request analytics, WhatsApp capacity/templates and preparation of existing workflows.
This is bounded composition of registered capabilities, not arbitrary action execution.
No schema migration or approved company fact change is needed. Model-written admin
summaries do not cross the WhatsApp customer-message boundary.

Phone searches normalize spaces, parentheses and separators without inferring a
country. The legacy conversation/delivery route is also repaired for saved callers
that still produce the earlier intent vocabulary.

## Verification

- HTTP/PostgreSQL/RLS tests: `apps/api/tests/test_admin_tasks.py`.
- Real-model/API/database acceptance: `apps/api/scripts/verify_admin_tasks.py`.
- Browser/API/model/database acceptance: `apps/web/e2e/admin_tasks.py`.
- The live acceptance scripts accept only local API/web targets and a disposable
  local test database. They create synthetic records and assert that no additional
  outbound message or business contact is created. The configured production model
  can be accessed through an SSH tunnel; its use does not deploy the candidate.
- Combined suite: **305 passed**, no skips, using the local disposable
  PostgreSQL database and restricted RLS role. Ruff, TypeScript checking,
  `git diff --check` and the Next.js production build passed.
- After adding bounded inference recovery and short-transcript preservation, all
  **21 task tests** passed again, including the added failure and omitted-topic cases.
- Approved company configuration validation passed with fingerprint
  `04c937047e8854a66ad223990aab9ab0f2c0ef35918a7f6895a32eff852616fd`.
- Actual `qwen3.8-27b` API checks passed for phone-formatted conversation retrieval,
  conversation + delivery + aggregate counts, reading + contact preparation, and
  explicit disclosure of unsupported live-stock lookup. The deployed model reports
  a 4096-token context; the new executor bounds its input accordingly.
- A fresh browser turn against the Next.js production build passed with the actual
  configured model and local API/database: two outcomes, expanded source messages,
  reload persistence, a single composer, mobile containment and no JavaScript errors.
  No browser response fixture was used. Earlier UI selector failures were repaired
  before this fresh run.
- Measured executor times in the API cases were about 43–76 seconds for reads and
  combined work, and 15 seconds for an unsupported capability. These exclude the
  initial planner call; this is functional acceptance, not a latency SLA.
- The Arch test host was unreachable. Tests ran locally; the real model was reached
  through the existing Windows host. The initial acceptance run did not change production. Deployment followed
  after the user explicitly requested it.

The workspace already contained unrelated changes before this work. Those changes
were already on production. Source comparison confirmed that the remaining
production differences were limited to this task feature.

## Production deployment

Deployed on 2026-09-14 after explicit authorization. Backup:
`C:\sites\ashiraai\backups\admin-tasks-20260914-201009`.
Build: `X50MlL6IF9ogY6jJf1u5x`. All 223 deployed file hashes matched.
Database backup completed; the private environment hash remained unchanged.
API, web, worker and recovery tasks restarted successfully. No schema migration.
HTTPS `/tr?tab=ops` returned 200. Database, Meta, configured Qwen, Redis and worker
preflight checks passed. The legacy WMIC host probe returned invalid output;
independent CIM CPU samples were 1%, 7%, 1%, below the preflight thresholds.
Generated staging was removed; rollback files and database backup are retained.

The original reported conversation request passed against the deployed code,
production database and configured Qwen: task → conversation, completed, verified
true, 64.1 seconds executor time. Verification used a transaction that was rolled
back, leaving no test chat/workflow records. No external message was sent.

## Gateway follow-up

The live "today's messages" turns failed semantic completion (167.8 and 212.4
seconds). They must not be reported as successful retrievals. IIS ARR still had
its 120-second default timeout, shorter than the executor and Next proxy budget.
On 2026-09-14 ARR timeout was set to 330 seconds at server level (the section is
AppHostOnly, so this applies to the shared gateway). Configuration backup:
`C:\sites\ashiraai\backups\gateway-timeout-20260914-201900`.
Frontend now converts HTML/malformed JSON into a readable ApiError without
automatically replaying a potentially running operation. Nine recovery tests
passed, including HTML HTTP 200/502/504. Build `jFALff_KL1dMOceRprnyr`,
216 file hashes verified; backup `C:\sites\ashiraai\backups\gateway-json-20260914-201951`.
Environment unchanged. Date-filtered cross-conversation retrieval remains an
observed functional gap; this gateway fix does not claim to solve it.
