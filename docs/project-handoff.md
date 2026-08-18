# Ashira AI — Project Handoff

> This document is the starting context for a fresh engineering session.
> It deliberately contains **no passwords, API tokens, private keys, or
> webhook verify tokens**. Never put those values in Git, a chat transcript,
> or this file.

> **Latest production checkpoint:** read [`runbook.md`](runbook.md), especially
> “Artı Kasnak canlı WhatsApp ajanı” and “2026-08-15 üretim kontrol noktası”.
> [`checkpoint-2026-08-01.md`](checkpoint-2026-08-01.md) is historical design
> context and does not override the current runtime state below.

## Current active scope — Artı Kasnak production completion

The Windows/Meta/database runtime has been implemented and deployed for the
Artı Kasnak tenant. The Arch host is online, its loopback-only reverse SSH
tunnel is installed and persistent, and a structured call through the actual
production runtime reached `qwen3:8b` without fallback. The remaining launch
blocker from shared-host CPU saturation was removed with owner authorization by
fully decommissioning the broken Publio deployment. The final
`runtime_preflight.py --require-ollama` run passed every gate with exit code
zero. Nimbus was preserved under a Publio-independent `SharedNodeApps` Windows
service.

Current verified state:

- webhook authentication is fail-closed and tenant/WABA/phone bound;
- one active tenant may own a non-null WABA binding, enforced by PostgreSQL;
- inbound messages create durable, deduplicated runtime jobs;
- the local model may return only an action plus approved fact IDs;
- trusted code renders literal approved customer text;
- ambiguous Meta sends are not automatically retried;
- an opt-out received during model inference cancels every not-yet-sent turn
  before the Meta POST boundary;
- human handoff pauses the bot and can be manually resumed;
- model-offline fallback gives approved Artı Kasnak contact details without
  falsely claiming a human has received the request;
- the production database uses a non-superuser, non-`BYPASSRLS` role and
  reapplies transaction-local tenant context after every checkout/commit;
- Artı Kasnak agent version 2 is LIVE/approved on tenant `kasnak`.

**LoRA, fine-tuning and training company knowledge into model weights are out
of scope.** The model must not become the source of company facts.

## 1. Product decision

Ashira AI is a multi-tenant WhatsApp agent platform. It must support any
company, not a fixed vertical such as elevator sheaves.

The customer-facing agent is constrained by a universal, versioned company
configuration. A local LLM creates language, but it is never the authority on
company facts.

Core principle:

```text
company JSON             = source of truth
local LLM                = intent + structured action proposal only
validator + renderer     = final authority before a customer receives a reply
```

No cloud LLM may receive customer messages. Raw customer messages can be
processed by the locally hosted model only.

For the strict path, the model does **not** write the customer-visible reply.
It returns only an action and approved fact IDs; trusted code renders literal
`customer_text` values from the currently approved JSON.

## 2. Universal company JSON

The canonical model is `CompanyAgentConfig`:

- source: `apps/api/src/modules/agents/company_config.py`
- documentation: `docs/company-agent-config.md`
- persistence: `agent_versions.company_config` (JSONB)
- schema endpoint: `GET /api/v1/agents/company-config/schema`

The abstract company representation is a typed graph:

```text
Company = (entities, relationships, facts, customer_profiles, processes,
           policies, agent_policy)
```

It supports an organization, arbitrary parties, products/services/projects,
relationships, customer fields, deterministic business processes, policies,
and named/versioned sector modules.

### Draft versus live contract

- A `draft` can be incomplete. Not every company needs every section.
- `approved`/live config is closed-world: every graph reference resolves.
- A live agent requires only:
  - organization display name in the default locale;
  - at least one explicit conversation purpose;
  - supported/default locale;
  - `require_fact_ids_for_claims = true`;
  - an explicit safe unknown-fact action.
- Products, prices, customer fields, processes and policies remain optional
  unless an enabled capability requires them.

`AgentService.promote_to_live()` validates this contract. Config is copied
with drafts and preserved on rollback.

### Mathematical framing

The goal is structural coverage, not a claim that one static form anticipates
every future field:

```text
span(schema) = company space
```

This is achieved through a closed typed core plus an explicit namespaced
module boundary, not a free-form top-level dictionary. Individual companies
are points/graphs within that space. Runtime customer state is separate from
the company blueprint.

## 3. LLM boundary — no fine-tuning decision

### Target runtime boundary (mandatory for customer-facing use)

Files:

- `apps/api/src/modules/agents/company_runtime.py`
- `apps/api/tests/test_company_agent_runtime.py`

The local-only experiment establishes this stronger contract. The model sees
only approved customer-visible facts; it receives no internal fact values,
sources, customer profiles or unregistered module data.

The model must emit:

```json
{
  "action": "answer | handoff",
  "fact_ids": ["approved-fact-id"]
}
```

The action-dependent JSON schema and a second controller validation enforce:

```text
fact_ids ⊆ customer_visible_facts
action = answer  ⇒  1 ≤ |fact_ids| ≤ 3
action = handoff ⇒  |fact_ids| = 0
```

The trusted renderer then produces the only customer text:

```text
reply = join(customer_text[fact_id] for fact_id in canonical(fact_ids))
```

Raw model text is never sent to a customer. This yields a provenance
guarantee: every factual customer clause comes from an approved JSON fact or
a fixed safe handoff template. It does not prove the model always chooses the
most relevant approved fact; that is measured separately with golden tests.

`company_runtime.py` implements this strict contract. It does not accept a
model-written customer reply. The WhatsApp path resolves the live agent,
persists audit/fact selection, renders approved text and executes durable
handoff/pause behavior. PostgreSQL-backed integration tests cover duplicate
webhooks, sender mismatch, RLS pool reuse, handoff pause/resume, ambiguous Meta
POST handling and out-of-order delivery callbacks.

### Training / LoRA policy

Do **not** use LoRA or fine-tuning in this project phase. In particular, do
not fine-tune company prices, availability, policies or other mutable facts
into model weights. Those belong in JSON + retrieval so one fact update is
immediately effective, reviewable and citeable.

For a very large config, compile customer-visible fact records into a local
retrieval index and give the model only the relevant facts. Keep exact fields
(e.g. price, availability, policy) queryable deterministically; vector search
is a relevance aid, not the source of truth.

For a strict no-hallucination path, the LLM may at most propose an action and
fact IDs. The backend/controller validates those IDs, then assembles the final
customer response from approved `customer_text` values and deterministic
templates. The LLM must not compose new factual clauses.

### Local Qwen E2E result — 2026-07-31

Files:

- `experiments/local_llm_json_e2e.py`
- `experiments/local_llm_json_e2e_fixtures.json`
- `experiments/README.md`
- `experiments/e2e-report-qwen2.5-7b.json`

The real local `qwen2.5:7b` model was evaluated through Ollama's native
structured-output endpoint, using three independent mock company topologies:
physical manufacturing, SaaS subscription and professional services. The
12-case set included direct/paraphrased/multi-fact questions, unknown facts,
private facts, prompt injection, cross-company requests and a legal request.

Results from the captured report:

- schema validity, visible-fact grounding, private-fact safety and
  deterministic renderer: **100%**;
- action accuracy: **100%**;
- exact fact-set accuracy / exact E2E pass rate: **91.67%**;
- fact-selection precision: **88.89%**, recall: **100%**, F1: **94.12%**;
- repeated direct/private/injection/cross-tenant scenarios were stable: **1.0**;
- mean local model latency: **0.686 s** (max **0.975 s**).

The only semantic miss was safe but over-broad: for a question asking the
free 30-minute discovery session, Qwen selected the exact duration fact plus
an irrelevant visible “online” format fact. The renderer still emitted only
approved JSON text; no private information or invented claim appeared.

Four metamorphic properties also passed: irrelevant visible-fact
noninterference, fact-order permutation invariance, immediate reflection of a
price update without training, and structural tenant fact-ID exclusion.

### Guided admin configuration result — 2026-07-31

The company owner must not be exposed to raw JSON or pushed through a fixed
linear form. The target is a separate `GuidedConfigSession` state machine over
the draft graph:

```text
admin message/file → local intent/proposal → preview → explicit acceptance → draft
```

It enforces an explicit collection gate: after adding an offering, the bot
asks whether another product/service/variant exists (or whether to import a
file) before moving to a distant configuration area. No silent answer means
“no more products.” Only an explicit close/defer signal may leave that
semantic neighborhood.

Files are evidence, not configuration authority:

```text
Excel/PDF → immutable artifact + hash + local extraction/evidence spans
          → candidate patches → admin preview/acceptance → draft
```

Imported facts default to `customer_visible: false`; they must be explicitly
made customer-visible and subsequently pass normal draft/review/approval
validation. File content cannot issue workflow instructions or publish a
config.

Local prototype and reports:

- `docs/admin-configuration-conversation.md`
- `experiments/guided_admin_config_e2e.py`
- `experiments/guided_admin_config_e2e_fixtures.json`
- `experiments/guided-admin-config-e2e-report.json`
- `experiments/admin_intent_e2e.py`
- `experiments/admin-intent-e2e-report-qwen2.5-7b.json`

The guided-session E2E passed 16/16 checks. A real Qwen intent-only test of
nine Turkish admin messages yielded 100% structured-output validity and 1.0
repeat stability. Raw intent accuracy was 88.89% because an overt policy
override injection was classified as `defer`; the deterministic controller
guard turned it into `unknown`, producing 100% guarded outcome accuracy. This
is why the model must never own workflow transitions, proposal acceptance or
publication.

### Intent-driven interactive surfaces — 2026-07-31

The admin conversation must be dynamic without becoming arbitrary. Model the
flow as:

```text
validated admin intent → session-derived InteractionGoal → InteractionPlan
                       → WhatsApp button/list/Flow/document/text renderer
```

`Intent` answers what the owner meant; `InteractionGoal` answers what the bot
must collect next; `Surface` answers how WhatsApp presents that goal. The
controller, not an LLM, chooses the goal from the active graph focus and hard
collection/preview/security gates.

Every button/list/Flow submission carries a short opaque server-side action
reference bound to tenant, admin, session, draft revision, question, allowed
transition, expiry and single-use nonce. Labels and client payloads never
authorize a config patch. A stale, forged, cross-admin or duplicate action is
rejected/no-op.

`experiments/interaction_policy_e2e.py` passed 24/24 local checks:

- product continuation → three reply buttons;
- five choices → list; structured price entry → Flow; no-Flow fallback → text;
- file request → document prompt rather than a config mutation;
- 17 proposal review → Flow or paginated fallback;
- channel rendering preserves the same semantic action set; and
- stale/expired/forged/cross-admin/cross-tenant/unenabled-publish actions have
  acceptance rate 0.

See `docs/intent-driven-interactions.md`,
`experiments/interaction_policy_e2e_fixtures.json` and
`experiments/interaction-policy-e2e-report.json`.

## 4. Existing builder state

There is already an admin builder scaffold:

- `apps/api/src/integrations/llm.py`
- `apps/api/src/modules/agents/builder.py`
- `apps/api/src/modules/agents/builder_service.py`
- router endpoints under `/api/v1/agents/{agent_id}/builder/...`

It has a local Ollama client and a legacy builder flow based on old columns
(`persona`, `tone`, `product_knowledge`, etc.). It is **not yet migrated** to
write the canonical `company_config` through a proposed JSON patch + preview
flow. This is a major next implementation task.

The real admin experience must be guided and non-technical:

1. admin describes the company in chat;
2. LLM proposes a narrow config patch;
3. backend validates it against the typed schema;
4. admin sees a natural-language preview / test conversation;
5. admin explicitly approves;
6. only then is the draft updated or promoted.

LLM output must never write config directly without validation and approval.

## 5. Local LLM host (Arch Linux laptop)

The local LLM runs on a separate Arch Linux laptop, not on the Windows API
server. It currently has:

- CPU: Intel i5-13500H (16 logical CPUs)
- RAM: 31 GiB
- GPU: NVIDIA RTX 4050 Laptop GPU (6 GiB VRAM)
- Ollama: `0.32.5`, system service active
- production model: `qwen3:8b` (5.2 GB)
- fallback model: `qwen2.5:7b` (4.7 GB)

Both model tags were downloaded successfully. The measurements immediately
below are the older `qwen2.5:7b` baseline; the current Qwen3 evaluation is in
`docs/qwen3-simulator-evaluation-2026-08-01.md`.

Measured local Ollama API performance:

- cold first request: ~29.5 s (includes ~15.4 s model load);
- warm request: ~0.77 s total for a short Turkish response;
- generation: ~41 tokens/s;
- prompt evaluation: ~770 tokens/s.

For low latency, keep the model resident between requests. A cold model load
is acceptable after reboot, but not for every WhatsApp turn.

### Arch SSH access

From the Mac development machine, an Ed25519 key was created at:

```text
~/.ssh/arch_llm_ed25519
```

The Arch host's private LAN address and user are intentionally not repeated
here. Use the existing SSH configuration/key or rediscover the current LAN IP
from the Arch machine. No private key is committed to this repository.

### Arch network observation

The initial “slow Ethernet” issue was diagnosed:

- wired link is 1 Gbit/s/full duplex;
- NIC is built-in Realtek RTL8111/8168 using `r8169`;
- the real immediate cause of high local latency was GNOME Software doing a
  large background Flatpak update (~1.2 MB/s), causing bufferbloat;
- after gracefully stopping GNOME Software, modem ping fell from hundreds of
  milliseconds to ~0.8 ms.

Do not assume the driver is faulty unless link-down events recur at idle.
If they recur, first test a different cable/router port, then evaluate the
Realtek driver in a planned maintenance window.

## 6. Production model network design

The Windows API server calls the Arch-local Ollama API through a loopback-only
reverse SSH tunnel. Neither side exposes port `11434` to the LAN or public
internet:

```text
Windows 127.0.0.1:11434 <-- reverse SSH tunnel <-- Arch 127.0.0.1:11434
```

The dedicated Windows account has public-key authentication, remote forwarding
only, no password authentication and no shell. The Arch systemd unit pins the
Windows SSH host key and keeps the tunnel alive. Deployment and recovery
commands are in `docs/runbook.md` and `apps/api/ops/{windows,arch}`.

The Windows Ashiraai `.env` uses:

```dotenv
LLM_PROVIDER=ollama
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=qwen3:8b
```

Do not set `OLLAMA_HOST=0.0.0.0`; Ollama must remain loopback-only.

## 7. WhatsApp / Windows server state

### Domain and webhook

- API domain: `api.ashiraai.com`
- webhook path: `/webhooks/whatsapp/kasnak`
- Meta webhook verification succeeded previously.
- IIS reverse proxy sends this domain to a local Uvicorn service on port 8001.

The callback is intentionally tenant-slugged. `kasnak` was only the initial
tenant path; the product architecture itself is not Kasnak-specific.

### Windows isolation rules

Ashiraai is isolated under `C:\sites\ashiraai` on the Windows server, with
its own PostgreSQL and Redis instances. Existing SeferTası/IIS sites,
PostgreSQL on the standard port, Redis on the standard port and all unrelated
Windows services must not be interrupted or restarted.

Important caveat: the API was proved reachable and webhook verification
worked. `AshiraaiApi`, `AshiraaiAgentWorker` and `AshiraaiAgentRecovery` are
scheduled and were verified running. The host is shared: unrelated Publio Node
processes have caused intermittent SSH/TLS handshakes to time out under high
CPU. Do not stop or limit those processes without explicit owner approval.

### Meta status

- production phone number is registered;
- webhook configuration is verified;
- the Artı Kasnak business verification screen was shown as verified;
- payment/templates are only needed for business-initiated messages outside
  WhatsApp’s customer-service window, not for a normal customer reply;
- a production System User access token, App Secret, phone-number ID and WABA
  ID are present in the secured Windows `.env` and live Meta reads succeeded.

Never paste these values into chat. The previously pasted temporary access
token was expired; do not reuse it. If a permanent Meta token is generated,
ask for confirmation immediately before clicking the UI button because it is
a persistent credential.

### Current webhook behavior

`apps/api/src/modules/outreach/webhooks.py` now:

- rejects missing/invalid HMAC signatures;
- requires an active tenant plus exact WABA and phone-number binding;
- deduplicates Meta inbound IDs and tenant-wide phone identities;
- creates/routes new inbound WhatsApp customers;
- persists ordered runtime jobs and coalesces rapid turns;
- invokes the strict company runtime and stores selected fact IDs/audit state;
- pauses a conversation on handoff and resumes only through the manual inbox;
- applies monotonic Meta status callbacks with their event timestamps.

## 8. Recommended next sequence

1. **Monitor the completed production runtime**
   - alert on Windows CPU saturation and tunnel/model reachability;
   - periodically run `runtime_preflight.py --require-ollama` and the signed
     webhook probe without creating customer-visible messages;
   - monitor the new `SharedNodeApps` service independently from Ashiraai.

2. **Migrate the remaining admin builder to canonical JSON**
   - replace legacy builder patch fields with validated `company_config`
     proposal patches;
   - implement preview and explicit admin approval;
   - build a non-technical WhatsApp/admin UI flow using replies/lists/Flows
     where suitable.

4. **Add operations around the implemented customer runtime**
   - provision active tenant reviewers and alerting/assignment SLAs;
   - expose manual-review/ambiguous-send jobs in an operator surface;
   - add metrics and alerts for queue depth, handoff age and callback errors.

## 9. Verification / worktree notes

The repository is intentionally dirty with user work and prior implementation
work. Preserve unrelated changes, especially existing web deletions and
untracked files. Do not reset/checkout broadly.

Relevant production additions include:

- `apps/api/src/modules/agents/company_config.py`
- `apps/api/src/modules/agents/company_runtime.py`
- `apps/api/tests/test_company_agent_config.py`
- `apps/api/tests/test_company_agent_runtime.py`
- `apps/api/tests/test_whatsapp_runtime_integration.py`
- `apps/api/src/workers/agent_runtime.py`
- `apps/api/scripts/runtime_preflight.py`
- `apps/api/scripts/runtime_webhook_probe.py`
- migrations through `c4f9128ab6d0`
- `docs/company-agent-config.md`

The current focused runtime/WhatsApp/RLS/preflight suite passes in a disposable
restricted-role PostgreSQL environment. The full API suite has one unrelated,
pre-existing `test_robots.py` discovery-policy failure; discovery automation is
disabled in this deployment. Preserve the dirty worktree and deploy only an
explicit runtime allowlist.

## 10. Security rules

- Never expose WhatsApp access tokens, app secrets, webhook tokens, passwords
  or SSH private keys.
- Do not store a sudo password in `.env`, shell history, a script or chat.
- Avoid broad/persistent passwordless sudo. If maintenance access is needed,
  use a time-bounded, command-whitelisted sudo rule and retain audit logs.
- Never expose Ollama publicly.
- Never restart or alter unrelated Windows services.
- Treat customer messages as untrusted input; no message may override agent
  policy, disclose internal config, or make uncited company claims.
