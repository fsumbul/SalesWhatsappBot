# LeadPulse — Project Summary

**Last updated:** 2026-07-20

Multi-tenant B2B lead generation & WhatsApp outreach SaaS platform.
First pilot vertical: **elevator sheave (asansör kasnağı) sales** — targeting Turkey, Germany, UK, UAE, Saudi Arabia, and Russia. The core is sector-agnostic: adapting to a new sector should take **less than 1 day** (config only).

---

## 1. Purpose

LeadPulse lets a company:

1. **Pick a sector** and define keywords, target customer types, and countries.
2. **Automatically discover B2B leads** from the internet (Google Places, SerpAPI, Bing, Overpass/OSM, directory scrapers).
3. **Enrich & verify** them — phone normalization (E.164), WhatsApp existence check, sector-fit scoring.
4. **Pass every lead through a compliance filter** — opt-out lists, cooldowns, quiet hours, IYS (TR), GDPR/KVKK audit logging.
5. **Send bulk outreach via the WhatsApp Business Cloud API** using Meta-approved templates, with sender warm-up and rate limiting.
6. **Manage replies in a CRM inbox** with conversation states, assignment, and a lead status funnel (discovered → contacted → replied → interested → quoted → won/lost).

### Business goals
- 1000+ verified B2B leads in the first 30 days of the pilot.
- 15%+ WhatsApp reply rate, 3%+ conversation → sales opportunity conversion.
- 99.5% uptime, < 5 s message send latency, < 5% template rejection rate.

## 2. Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Celery + Redis |
| Frontend | Next.js 15 (App Router), TypeScript, Tailwind, shadcn/ui |
| Database | PostgreSQL 16 with Row-Level Security (tenant isolation) |
| Infra | Docker Compose, Caddy, GitHub Actions, Sentry, Grafana/Loki |
| Integrations | Meta WhatsApp Cloud API, Google Places, SerpAPI, Bing, Overpass, IYS |
| i18n | 5 languages: TR, EN, DE, AR, RU |

Monorepo: `apps/api` (FastAPI), `apps/web` (Next.js), `packages/shared` (OpenAPI-generated types), `infra/`, `docs/`.

## 3. Current Status

Implemented (code exists in the repo):

- **API modules:** auth (JWT, multi-tenant, RBAC), sectors, discovery, compliance, outreach, reports.
- **Integrations:** WhatsApp Cloud API, Google Places, SerpAPI, Bing, Overpass, IYS, shared rate limiting.
- **Celery workers:** discovery, enrichment, outreach, maintenance.
- **Web app pages:** dashboard, sectors, campaigns, leads, inbox, templates, senders, compliance, reports, login/register — all locale-aware (`[locale]/…`).
- **Docs:** architecture, compliance, runbook (see `docs/`).

See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the full phase-by-phase roadmap (Faz 0–10).

## 4. TODOs

### Major roadmap items

1. **Self-service agent building over WhatsApp** *(TR: "Her müşterinin adminden WhatsApp üzerinden kendi agent'ını kendisinin geliştirmesi")*
   Each customer (tenant) should be able to build and evolve **their own AI agent by themselves**, through the admin, over WhatsApp. Instead of the platform team configuring agents, the tenant converses with the system on WhatsApp to define/refine their agent's behavior (sector knowledge, tone, reply logic, qualification questions). This turns agent setup into a self-service, chat-driven flow.
   - Design an agent-definition model per tenant (persona, product knowledge, languages, guardrails).
   - Build a WhatsApp-based configuration conversation flow ("agent builder bot") exposed through the admin.
   - Version and test agent configs before they go live on real conversations.

2. **Keyword-driven web scraping for faster, larger-scale data discovery** *(TR: "Anahtar keyword'ler sayesinde internetten web scraping ile verilerin hızlı ve daha fazla bulunması")*
   Use the sector's key keywords to drive broader web scraping so lead data is found **faster and in higher volume** than the current API-connector approach alone.
   - Expand the Playwright-based scraper framework beyond directories (Kompass, Europages, AYSAD) to general keyword-targeted crawling.
   - Multi-language query expansion from sector keywords (TR/EN/DE/AR/RU).
   - Respect robots.txt, add rotating proxies + human-like delays to avoid IP bans.
   - Feed results through the existing dedup (fuzzy company name + domain) and enrichment pipeline.

### From the project plan (remaining / V2+)

- [ ] AI message personalization and AI auto-reply (V2 — partially unlocked by TODO #1).
- [ ] E2E test coverage, load testing (k6), OWASP security audit (Faz 9).
- [ ] Onboarding wizard.
- [ ] Billing / Stripe integration (V2).
- [ ] Email outreach, LinkedIn automation, voice call integration (V2).
- [ ] Mobile app (V2).
- [ ] Pilot launch with the elevator sheave vertical + metric-driven iteration (Faz 10).

## 5. Engineering Principles

**Structural, not ad-hoc — but simple, not overengineered.** Every change is the smallest one that fits the architecture: problems are fixed at the layer that owns them (compliance rules in the compliance engine, lead sources behind the connector interface), shortcuts require a tracked debt marker, and structural decisions get a one-paragraph note in `docs/`. At the same time: the project stays a modular monolith (Postgres + Redis + Celery, no microservices/Kafka), no speculative abstractions before the third concrete case exists, and no new dependency unless it replaces meaningful code. Full version in [ROADMAP.md](ROADMAP.md#engineering-principles-apply-to-every-phase).

## 6. Key Risks

| Risk | Mitigation |
|---|---|
| WhatsApp template rejection / sender ban | Multiple template variants, warm-up algorithm, multi-sender pool |
| Scraping IP bans | Rotating proxies, Playwright with human-like delays, robots.txt compliance |
| KVKK/GDPR complaints | Corporate numbers only, IYS check, fast opt-out, full audit log |
| Multi-tenant data leakage | PostgreSQL RLS + middleware + tests |
