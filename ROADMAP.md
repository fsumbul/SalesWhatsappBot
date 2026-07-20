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

- [ ] **Unit tests** for the highest-risk logic: compliance engine (opt-out, cooldown, quiet hours), phone normalization, fit scoring, dedup fuzzy matching. Target ≥70% on `modules/compliance` and `modules/discovery`.
- [ ] **Integration tests** with Testcontainers (Postgres + Redis): RLS tenant isolation, auth flows, lead status machine transitions.
- [ ] **Mock-based integration tests** for each external connector (WhatsApp, Places, SerpAPI, Bing, IYS) using recorded fixtures.
- [ ] **One E2E happy path** (Playwright): register → create sector → run discovery (mocked) → lead appears → campaign created.
- [ ] CI gates: tests + lint must pass to merge; add coverage reporting.

**Exit criteria:** CI green with the above suites; RLS isolation proven by a test that fails when policies are dropped.

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

- [ ] **Query expansion service:** generate multi-language search queries (TR/EN/DE/AR/RU) from sector keywords; store per-sector query sets.
- [ ] **Generic Playwright crawler framework:** keyword-targeted crawling beyond the fixed directories (Kompass, Europages, AYSAD) — search-result harvesting, contact-page detection, structured extraction.
- [ ] **Anti-ban infrastructure:** rotating proxy pool, human-like delays, per-domain rate limits, robots.txt compliance (hard requirement).
- [ ] **Pipeline integration:** scraped candidates flow through existing dedup (fuzzy name + domain) → enrichment → fit scoring; source attribution kept on every lead.
- [ ] **Quality dashboard:** per-source precision (share of scraped leads that pass qualification) so bad sources get pruned.
- [ ] Scale-out knob: Celery worker autoscaling for crawl jobs.

**Exit criteria:** scraping pipeline contributes ≥2× the lead volume of API connectors at ≥ comparable qualification rate, with zero robots.txt violations.

## Phase E — Self-Service WhatsApp Agent Builder (Weeks 9–14)

> TODO #1 — *"Her müşterinin adminden WhatsApp üzerinden kendi agent'ını kendisinin geliştirmesi."*
> Goal: each tenant builds and evolves their own AI agent themselves, through a chat-driven flow on WhatsApp. Also unlocks the V2 "AI auto-reply" item.

**E1 — Agent definition model (Weeks 9–10)**
- [ ] Per-tenant agent schema: persona/tone, product knowledge base, languages, qualification questions, guardrails (forbidden topics, escalation rules), reply policies.
- [ ] Versioned configs (draft → testing → live) with rollback; audit log of who changed what.

**E2 — Agent builder bot (Weeks 10–12)**
- [ ] WhatsApp conversation flow, launched from the admin panel, where the tenant *describes* their agent conversationally ("agent builder bot"); LLM translates the dialogue into the agent definition.
- [ ] Builder supports iteration: tenant sends feedback messages, config updates as a new draft version.
- [ ] Admin UI mirror: view/edit the same definition in the web panel (fallback for non-chat editing).

**E3 — Sandbox & runtime (Weeks 12–14)**
- [ ] **Sandbox mode:** tenant chats with their draft agent on WhatsApp before going live; test transcripts saved.
- [ ] **Runtime:** live agent answers inbound conversation messages within its guardrails; hands off to a human (Inbox assignment) on low confidence, escalation triggers, or explicit request.
- [ ] Per-tenant usage metering (LLM cost tracking) — prerequisite for billing later.
- [ ] Safety review: prompt-injection hardening, PII handling, compliance-filter applies to agent replies too.

**Exit criteria:** one pilot tenant builds an agent end-to-end over WhatsApp without developer help; agent handles ≥50% of inbound messages without human takeover, with clean handoffs on the rest.

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
