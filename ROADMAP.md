# LeadPulse — End-to-End Roadmap

**Created:** 2026-07-20 · **Horizon:** ~6 months (pilot live by ~Week 6, both major TODOs shipped by ~Week 14)

This plan takes the project from its **current state** (feature-complete core, minimal tests, never run against live external services) through **pilot launch** and both major roadmap items from [SUMMARY.md](SUMMARY.md). It supersedes the week numbering in [PROJECT_PLAN.md](PROJECT_PLAN.md) Faz 0–8, which are largely built.

## Where we are today

| Area | State |
|---|---|
| API modules (auth, sectors, discovery, compliance, outreach, reports) | ✅ Implemented |
| Integrations (WhatsApp, Google Places, SerpAPI, Bing, Overpass, IYS) | ✅ Implemented, ❌ unverified against live APIs |
| Celery workers (discovery, enrichment, outreach, maintenance) | ✅ Implemented |
| Web app (all pages, 5 locales) | ✅ Implemented |
| Automated tests | ❌ Only `test_health.py` — biggest risk in the repo |
| Production deployment | ❌ Not deployed; no Meta Business verification yet |

---

## Phase A — Stabilization & Test Foundation (Weeks 1–2)

> Goal: trust the code we already have. Nothing ships on top of an untested core.

- [x] **Unit tests** for the highest-risk logic: compliance engine (opt-out, cooldown, quiet hours), phone normalization, fit scoring, dedup fuzzy matching, query generation. `modules/compliance` ~85%, `modules/discovery` ~76% — both above the 70% target.
- [x] **Integration tests** with a real Postgres: RLS tenant isolation (the P0 regression test), compliance engine end-to-end. Auth flows and lead-status-machine transitions are *not* covered yet — narrower gap than the original item implied.
- [x] **Mock-based integration tests** for each external connector (WhatsApp, Google Places, SerpAPI, Bing, Overpass, IYS) using respx-mocked HTTP fixtures — including the free-scrape HTML regex parsing path for Bing, the most fragile code in that connector.
- [ ] **One E2E happy path** (Playwright): register → create sector → run discovery (mocked) → lead appears → campaign created. Not attempted — no frontend test infrastructure exists yet (no Playwright config, no test-mode dev-server wiring); this is a separate initiative, not a quick add.
- [x] CI gates: lint (ruff) + type-check (mypy) + tests must pass — and now actually do (both had never been green before this phase). Coverage reporting wired in (term output + XML artifact per CI run), not gated on a hard threshold — total repo coverage (~63%) isn't realistic to gate yet given large untested surfaces (outreach dispatch, webhooks, auth service).

**Exit criteria:** CI green with the above suites (✅); RLS isolation proven by a test that fails when policies are dropped (✅ — also caught a real bypass bug, see commit history).

## Phase B — Production Infrastructure & External Go-Live (Weeks 2–3, overlaps A)

> Goal: a deployed staging + production environment with real credentials.

- [ ] Hetzner servers provisioned (CX32 app + CPX21 db), Docker Compose prod config, Caddy SSL, domain + Cloudflare.
- [ ] Sentry + Grafana/Loki wired; alerting to Telegram/Slack.
- [ ] **Meta Business Manager verification** (longest lead time — start immediately), WhatsApp Business phone number, Cloud API credentials.
- [ ] First message **templates submitted for Meta approval** (3 variants each, utility category preferred; TR + EN first).
- [ ] Live API keys: Google Places, SerpAPI, Bing, IYS; quota alarms configured.
- [ ] Webhook endpoint live and verified with Meta (delivery receipts + inbound messages).
- [ ] Daily DB snapshot + weekly off-site backup.

**Exit criteria:** a real WhatsApp template message sent from production to a test number, delivery receipt received via webhook.

## Phase C — Pilot Launch: Elevator Sheave (Weeks 4–6)

> Goal: real leads, real messages, real metrics — small and compliant.

- [ ] Seed the pilot sector config: keywords, target customer types, countries (TR first, then DE/UK/UAE/SA/RU).
- [ ] Discovery runs against live APIs; manually review first ~200 leads for quality (fit score calibration).
- [ ] Compliance dry-run: verify IYS checks, quiet hours per timezone, opt-out flow with STOP/DUR keywords.
- [ ] Outreach with **warm-up schedule**: start ~20 msgs/day/sender, scale per Meta tier limits.
- [ ] Inbox staffed; measure reply handling latency.
- [ ] Weekly metric review: leads discovered, contact rate, reply rate (target 15%+), opportunity conversion (target 3%+), template rejection (<5%), sender health.

**Exit criteria:** 1000+ verified leads and 30 days of send history with no compliance incidents and no sender ban.

## Phase D — Keyword-Driven Web Scraping Engine (Weeks 6–9)

> TODO #2 — *"Anahtar keyword'ler sayesinde internetten web scraping ile verilerin hızlı ve daha fazla bulunması."*
> Goal: multiply lead volume beyond API connectors. Sequenced first of the two TODOs because the pilot directly benefits from more leads.

- [x] **Query expansion service:** reused the existing multi-language `query_generator.generate_queries()` (already covers TR/EN/DE/AR/RU per sector keywords) rather than building a parallel system — added `include_web_crawl` param so it also emits crawler-targeted queries. Per-sector *persisted* query sets are **not** built — today's queries are generated fresh per discovery run, which has been fine for every other connector; revisit only if a real need for storing/editing query sets shows up.
- [x] **Generic Playwright crawler framework:** `src/integrations/web_crawler.py` — config-driven seed URLs (not hardcoded), generic link/candidate extraction, contact extraction reusing `src/core/text_extract.py`. Verified end-to-end against a local test server (real Chromium, real robots.txt enforcement) during development.
  - **Real finding, not an assumption:** Kompass and Europages — the two directories this item originally named — turned out to disallow generic crawlers in their own robots.txt (Europages: blanket `Disallow: /`; Kompass: disallows `/c/` and `/search*`, i.e. the paths that actually matter). Hardcoding either would violate the exit criteria below or scrape nothing. The framework is deliberately seed-agnostic; operators configure `WEB_CRAWL_SEED_URLS` with sites they've confirmed permit crawling.
- [x] **Anti-ban infrastructure:** `src/core/robots.py` (fails closed on fetch failure, allows on confirmed 404 — this is load-bearing, tested), the existing per-connector `TokenBucket` reused for `web_crawl`, and a randomized 1–3s human-like delay between page visits. **Rotating proxy pool:** the round-robin mechanism is built (`WEB_CRAWL_PROXIES`), but no proxy provider account exists — needs an operator decision on a provider before this does anything.
- [x] **Pipeline integration:** `web_crawl` plugs into `CONNECTOR_MAP` in `workers/discovery.py` like any other connector — it flows through the *same* dedup/enrichment/fit-scoring path with zero special-casing, since it just yields `RawLead` objects. This is really a validation that the existing architecture's connector abstraction works, not new pipeline code.
- [~] **Quality dashboard:** `GET /api/v1/reports/source-precision` scaffolded (service + schema + router, tested against a real Postgres) — but it can't yet report a trustworthy number. `workers/enrichment.py::_enrich_campaign` hard-deletes sub-threshold `Lead` rows (and `LeadSource` cascades on delete), so the "how many did we ever try from this source" denominator doesn't survive in the DB. The endpoint documents this in its own response payload (a `caveat` field) rather than silently returning a misleading number. **Needs an operator decision:** either stop hard-deleting sub-threshold leads (soft-delete via status only) or add a small append-only per-source counter independent of the leads table's lifecycle. No UI built (that's a web-app task).
- [ ] Scale-out knob: Celery worker autoscaling for crawl jobs. Not started — there's no deployment target to autoscale yet (Phase B).

**Exit criteria:** scraping pipeline contributes ≥2× the lead volume of API connectors at ≥ comparable qualification rate, with zero robots.txt violations. **Not yet measurable** — `WEB_CRAWL_SEED_URLS` ships empty by design (see finding above), so there's no real seed site configured or pilot data to compare against yet. The robots.txt half of "zero violations" is enforced in code and tested; the volume/qualification-rate half needs real seeds and, per the quality-dashboard gap above, a decision on how to measure qualification rate accurately at all.

## Phase E — Self-Service WhatsApp Agent Builder (Weeks 9–14)

> TODO #1 — *"Her müşterinin adminden WhatsApp üzerinden kendi agent'ını kendisinin geliştirmesi."*
> Goal: each tenant builds and evolves their own AI agent themselves, through a chat-driven flow on WhatsApp. Also unlocks the V2 "AI auto-reply" item.

**E1 — Agent definition model (Weeks 9–10)** ✅ scaffolded
- [x] Per-tenant agent schema: persona/tone, product knowledge base, languages, qualification questions, guardrails (forbidden topics, escalation rules), reply policies. `src/modules/agents/` — `Agent` + `AgentVersion`, following the same module conventions (models/schemas/service/router) as every other bounded context.
- [x] Versioned configs (draft → testing → live) with rollback; audit log of who changed what. History is immutable: editing only ever mutates the current draft row; promotion moves a status flag; rollback clones the target version's *content* into a brand-new version rather than resurrecting the old row, so "a rollback happened" stays visible in the history forever. Audit trail reuses the existing `compliance.models.AuditLog` table rather than a parallel mechanism. Verified against a real Postgres: full lifecycle (create → edit → promote → promote → rollback → history intact) + RLS tenant isolation (`tests/test_agents.py`, 7 tests).

**E2 — Agent builder bot (Weeks 10–12)** 🟡 mechanics built and tested; not live
- [x] Conversation flow, launched from the admin panel (`POST /agents/{id}/builder/sessions`, `POST .../messages`) where the tenant *describes* their agent conversationally; the LLM's structured JSON output (`{"reply": ..., "draft_patch": ..., "ready_to_promote": ...}`) is strictly parsed and validated, never passed through — see `src/modules/agents/builder.py` (`parse_llm_response`, `build_system_prompt`) and `builder_service.py` (`AgentBuilderService`).
- [x] Builder supports iteration: every message is appended to `BuilderSession.messages` and the *full* transcript is replayed to the LLM each turn, so context carries across turns. Config updates go through `AgentService.update_draft` (the same E1 codepath the admin UI would use) — no parallel write path.
- [ ] Admin UI mirror in the web panel — not built (frontend work, separate from this backend scaffold).
- **Not live**, for two separable reasons:
  1. **No real LLM provider.** `NullLLMClient` (the only implementation) raises `LLMNotConfiguredError` on every call — every builder endpoint 503s today. Needs an operator decision: which provider (Anthropic, OpenAI, ...), and an API key/budget. Same category of external blocker as the Meta Business verification gating Phase B — not something to default or silently work around.
  2. **No WhatsApp inbound routing.** The endpoints above are the admin-panel-facing surface; nothing yet decides "this inbound WhatsApp message should go to the builder-bot session, not the normal outreach conversation or (once E3 exists) the live runtime agent." That's a routing/product decision, not built.
- Verified without a real LLM: 14 pure parser tests (valid/fenced/malformed JSON, unknown fields, wrong types) + 6 DB-backed service tests using a scripted stub `LLMClient` — session creation, patch application, multi-turn transcript replay, conflict on inactive session, and both failure paths (`LLMNotConfiguredError` → 503, malformed LLM output → 502) all return clean errors instead of crashing (`tests/test_agent_builder_parser.py`, `tests/test_agent_builder_service.py`).

**E3 — Sandbox & runtime (Weeks 12–14)** ❌ not started — blocked on E2
- [ ] **Sandbox mode:** tenant chats with their draft agent on WhatsApp before going live; test transcripts saved.
- [ ] **Runtime:** live agent answers inbound conversation messages within its guardrails; hands off to a human (Inbox assignment) on low confidence, escalation triggers, or explicit request.
- [ ] Per-tenant usage metering (LLM cost tracking) — prerequisite for billing later.
- [ ] Safety review: prompt-injection hardening, PII handling, compliance-filter applies to agent replies too.

**Exit criteria:** one pilot tenant builds an agent end-to-end over WhatsApp without developer help; agent handles ≥50% of inbound messages without human takeover, with clean handoffs on the rest. **Not reachable without E2/E3**, which are blocked on the LLM provider decision above.

## Phase F — Hardening & V2 Commercial (Weeks 14+)

- [ ] Load testing (k6, 1000 concurrent conversations) + OWASP ZAP scan in CI; fix findings.
- [ ] Onboarding wizard (self-service tenant setup — pairs with the agent builder).
- [ ] Billing: Stripe subscriptions + WhatsApp/LLM usage-based charges (uses Phase E metering).
- [ ] Second vertical onboarded (< 1 day config target — validates the sector-agnostic promise).
- [ ] Then, by demand: e-mail outreach, LinkedIn automation, voice, mobile app.

---

## Milestones at a glance

| Week | Milestone |
|---|---|
| 2 | CI-gated test suite green; core logic trusted |
| 3 | Production live; first real WhatsApp template delivered |
| 6 | Pilot complete: 1000+ leads, 30-day send history, no compliance incidents |
| 9 | Scraping engine doubles lead volume |
| 14 | First tenant self-builds an agent over WhatsApp; auto-reply live |
| 16+ | Billing live; second vertical onboarded |

## Engineering principles (apply to every phase)

**No ad-hoc solutions — but no overengineering either.** Every change should be the *smallest change that fits the architecture*. The two failure modes we're steering between:

**Structural, not ad-hoc:**
- Fix problems at the layer that owns them. A compliance rule goes in the compliance engine — never special-cased inside a worker or an if-branch in a route. A new lead source goes through the connector interface (`integrations/base.py`), not a one-off script.
- New behavior gets a schema, a migration, and a test — not a JSON blob column and a hardcoded constant "for now".
- If a deadline forces a shortcut, it's allowed only with a written debt marker: a `# DEBT:` comment + an issue describing the structural fix. Untracked hacks are not allowed.
- Structural decisions (new module, new dependency, new external service) get a one-paragraph decision note in `docs/` — what was chosen, what was rejected, why. A paragraph, not a template.

**Simple, not software-engineermaxxing:**
- **Stay a modular monolith.** No microservices, no Kafka, no new datastores — Postgres + Redis + Celery until a *measured* limit says otherwise.
- **No speculative abstraction.** Don't build for the third sector, the tenth integration, or the imagined scale before the second one actually exists (rule of three: extract the abstraction when the third concrete case appears).
- **Boring > clever.** Prefer the stack we already run; new dependencies must replace meaningful code, not decorate it.
- **YAGNI applies to process too:** no new tooling, config layers, or "frameworks for the framework" unless a current phase's exit criteria needs it.

**Tiebreaker when the two pull against each other:** design the interface structurally (module boundary, typed schema, clear ownership), keep the implementation behind it as simple as possible. Interfaces are hard to change later; implementations are cheap to change.

## Cross-cutting rules (apply to every phase)

- **Compliance is a gate, not a feature:** no send path may bypass opt-out/cooldown/quiet-hours checks — including agent-generated replies (Phase E).
- **Tenant isolation regression test** runs in CI forever.
- **Every new lead source** must pass through dedup + enrichment + compliance; no direct-to-outreach shortcuts.
- Weekly demo + metric review at each phase boundary; a phase's exit criteria must be met before the next phase's outreach-facing work starts (infra/test work may overlap).

## Top dependencies & risks

| Item | Why it can slip the plan | Action |
|---|---|---|
| Meta Business verification | Can take weeks; blocks Phases B–E | Start day 1 |
| Template approvals | Rejections block outreach | 3 variants/template, utility category |
| Proxy quality (Phase D) | Bad proxies → bans → no scraped leads | Budget for a reputable rotating-proxy provider |
| LLM costs (Phase E) | Per-tenant agents can get expensive | Metering from day one, caps per tenant |
| Test debt (today) | Any refactor is risky until Phase A lands | Phase A is non-negotiable, nothing ships before it |
