# B2B Sector-Based Lead Generation & WhatsApp Outreach Platform

**Kod Adı:** LeadPulse (öneri, değiştirilebilir)
**İlk Pilot Sektör:** Asansör Kasnağı
**Mimari Hedef:** Multi-tenant SaaS-ready
**Doküman Sürümü:** 1.0
**Tarih:** 1 Temmuz 2026

---

## 1. Yönetici Özeti

Kullanıcıların **sektör seçip**, o sektöre uygun B2B firmaları internetten otomatik bulup, doğrulayıp, yasal uyumluluk filtresinden geçirdikten sonra **WhatsApp Business API** üzerinden onaylı şablonlarla toplu satış mesajı gönderebildiği; gelen cevapları CRM inbox'ında yönetebildiği bir **çok kiracılı (multi-tenant) SaaS platformu**.

İlk dikey: **Asansör kasnağı** satışı. Türkiye + Almanya + İngiltere + BAE + Suudi Arabistan + Rusya pazarları.

---

## 2. Hedefler ve Başarı Kriterleri

### İş Hedefleri
- Pilot sektörde ilk 30 günde **1000+ doğrulanmış B2B lead** üretmek.
- **%15+ WhatsApp yanıt oranı** (B2B outbound için yüksek).
- **%3+ konuşmadan satış fırsatı** dönüşümü.
- Yeni sektöre uyarlama süresi: **< 1 gün** (sadece config).

### Teknik Hedefler
- 99.5% uptime.
- Mesaj gönderim gecikmesi < 5 sn.
- WhatsApp template rejection oranı < %5.
- KVKK/GDPR audit-ready log yapısı.

---

## 3. Kapsam

### Kapsam İçi (MVP)
- Sektör yönetimi (CRUD)
- Anahtar kelime / hedef müşteri tipi yönetimi
- Google Places + SerpAPI + Bing + dizin scraper'ları ile lead discovery
- Telefon normalize + WhatsApp doğrulama
- Sektör uyum skoru
- Compliance engine (opt-out, cooldown, quiet hours, IYS hook)
- WhatsApp Business Cloud API entegrasyonu (template gönderim + webhook)
- CRM inbox (konuşma yönetimi)
- Kampanya yönetimi
- Multi-tenant kullanıcı/rol yönetimi
- Temel raporlama
- 5 dil desteği: TR, EN, DE, AR, RU

### Kapsam Dışı (V2+)
- AI mesaj kişiselleştirme
- AI otomatik cevap
- Sesli arama entegrasyonu
- E-posta outreach
- LinkedIn otomasyonu
- Faturalama / Stripe entegrasyonu
- Mobil app

---

## 4. Kullanıcı Rolleri

| Rol | Yetki |
|---|---|
| **Super Admin** | Platform sahibi. Tüm tenant'ları görür. |
| **Tenant Owner** | Şirket sahibi. Kullanıcı davet eder, faturalama, ayarlar. |
| **Sales Manager** | Kampanya oluşturur, lead onaylar, raporları görür. |
| **Sales Agent** | Inbox'ta konuşma yönetir, lead onaylar. |
| **Viewer** | Sadece okuma. |

---

## 5. Sistem Mimarisi

### Yüksek Seviye Bileşenler

```
┌──────────────────────────────────────────────────────────────┐
│                      NEXT.JS FRONTEND                        │
│      (Admin Panel + CRM Inbox + Campaign Studio)             │
└─────────────────────────┬────────────────────────────────────┘
                          │ REST + WebSocket
                          ▼
┌──────────────────────────────────────────────────────────────┐
│                   FASTAPI GATEWAY                            │
│    Auth (JWT) | Rate Limit | Tenant Isolation | RBAC         │
└─┬──────────────┬─────────────┬────────────┬────────────┬─────┘
  │              │             │            │            │
  ▼              ▼             ▼            ▼            ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ Sector   │ │ Discovery│ │Compliance│ │ Outreach │ │  Inbox   │
│ Service  │ │ Service  │ │  Service │ │ Service  │ │ Service  │
└────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
     │            │             │            │            │
     └────────────┴──────┬──────┴────────────┴────────────┘
                         ▼
              ┌────────────────────┐
              │   POSTGRES + Redis │
              │   (Multi-tenant)   │
              └────────────────────┘
                         ▲
                         │
    ┌────────────────────┴─────────────────────┐
    │              CELERY WORKERS               │
    │  discovery | enrichment | outreach | AI   │
    └───────────────────────────────────────────┘
                         ▲
                         │
    ┌────────────────────┴─────────────────────┐
    │           EXTERNAL INTEGRATIONS           │
    │  Meta WA Cloud API | Google Places |      │
    │  SerpAPI | Bing | Kompass | IYS | libphn  │
    └───────────────────────────────────────────┘
```

### Servis Sorumlulukları

| Servis | Sorumluluk |
|---|---|
| **Sector Service** | Sektör profili, keywords, target customer types, dil ayarları |
| **Discovery Service** | Query generation, API çağrıları, lead scraping orchestration |
| **Enrichment Service** | Phone normalization, WA existence check, website analysis, scoring |
| **Compliance Service** | Opt-out list, cooldown, quiet hours, IYS check, blacklist |
| **Outreach Service** | Template management, WhatsApp send, warmup, rate limit |
| **Inbox Service** | Conversation state, webhook handler, message routing |
| **Reporting Service** | Kampanya metrikleri, funnel analizi |
| **Auth Service** | JWT, RBAC, tenant isolation |

---

## 6. Teknoloji Stack

### Backend
- **Python 3.12 + FastAPI** — API gateway ve tüm servisler
- **Celery + Redis** — Job queue, worker orchestration
- **PostgreSQL 16** — Ana veritabanı (Row-Level Security ile tenant izolasyonu)
- **Redis** — Cache + queue + pub/sub
- **Alembic** — DB migrations
- **Pydantic v2** — Data validation
- **SQLAlchemy 2.x** — ORM
- **httpx** — Async HTTP client
- **libphonenumber-python** — Telefon normalize

### Frontend
- **Next.js 15 (App Router) + TypeScript**
- **Tailwind CSS + shadcn/ui**
- **TanStack Query** — Server state
- **Zustand** — Client state
- **socket.io-client** — Realtime inbox
- **next-intl** — 5 dilli i18n

### Infrastructure
- **Hetzner CX32** (2 vCPU, 8GB RAM, ~€7/ay) — MVP başlangıç
- **Docker + Docker Compose** — Container orchestration
- **Caddy** — Reverse proxy + otomatik SSL
- **GitHub Actions** — CI/CD
- **Sentry** — Error tracking
- **Grafana + Loki** — Log/metric

### External Services
- **Meta WhatsApp Business Cloud API** — Mesajlaşma
- **Google Places API** — Firma discovery
- **SerpAPI** — Google search
- **Bing Web Search API** — Yedek arama
- **Playwright** — Sektör dizinleri scrape (robots.txt uyumlu)
- **IYS API** — Türkiye ticari ileti kontrolü

---

## 7. Veritabanı Şeması (Özet)

```sql
-- Multi-tenancy core
tenants (id, name, plan, wa_business_account_id, created_at)
users (id, tenant_id, email, password_hash, role, ...)

-- Sector configuration
sectors (id, tenant_id, name, product, description, default_language)
sector_keywords (id, sector_id, keyword, language, type) -- positive/negative
sector_target_customers (id, sector_id, customer_type, language)
sector_countries (id, sector_id, country_code, timezone)

-- Discovery & leads
campaigns (id, tenant_id, sector_id, name, status, filters_json, quotas)
leads (id, tenant_id, sector_id, campaign_id, company_name, website,
       country, city, source, source_url, fit_score, status)
lead_contacts (id, lead_id, type, raw_value, e164_value, is_whatsapp,
               consent_status, verified_at)
lead_enrichment (id, lead_id, employees_est, categories, meta_json)

-- Messaging
message_templates (id, tenant_id, sector_id, language, customer_type,
                   wa_template_name, wa_template_status, body, header, footer)
outreach_jobs (id, tenant_id, lead_id, contact_id, template_id,
               scheduled_at, status, wa_message_id, error)
conversations (id, tenant_id, lead_id, contact_id, status,
               last_message_at, assigned_user_id)
messages (id, conversation_id, direction, wa_message_id, type, body,
          media_url, status, timestamp)

-- Compliance
opt_outs (id, tenant_id, phone_e164, source, reason, created_at)
compliance_checks (id, lead_id, check_type, result, details_json, checked_at)
audit_logs (id, tenant_id, actor_id, action, entity, entity_id, ip, meta)

-- Rate limiting & warmup
sender_profiles (id, tenant_id, wa_phone_number_id, daily_limit,
                 current_tier, warmup_stage, last_reset_at)
```

**Multi-tenant izolasyon:** Tüm ana tablolarda `tenant_id` + PostgreSQL RLS (Row-Level Security) policy'leri.

---

## 8. Lead Status Machine

```
discovered ─▶ enriched ─▶ qualified ─▶ ready_to_contact
                              │
                              ├──▶ blocked_by_compliance
                              │
                              └──▶ contacted ─▶ delivered ─▶ read
                                                    │
                                                    ├──▶ replied ─▶ interested ─▶ quoted ─▶ won
                                                    │                                     └─▶ lost
                                                    └──▶ not_interested
                                                    └──▶ blacklisted
```

---

## 9. Yol Haritası (Fazlar)

### Faz 0 — Hazırlık (Hafta 1)
- Repo kurulumu (monorepo: `apps/api`, `apps/web`, `packages/shared`)
- Docker Compose dev environment
- CI/CD pipeline (test + lint + build)
- Sentry + Loki kurulumu
- Domain + Cloudflare + Caddy SSL
- Meta Business Manager doğrulama (varsa hazır)
- API anahtarları (SerpAPI, Google, Bing)

### Faz 1 — Multi-tenant Auth & Core (Hafta 2-3)
- Tenant + User + Role tabloları
- JWT auth, refresh token, invitation flow
- RLS policies
- Base API + frontend layout
- i18n altyapı (5 dil)

### Faz 2 — Sector Configuration (Hafta 4)
- Sector CRUD (UI + API)
- Keyword / target customer / country management
- Sektör şablonu import/export

### Faz 3 — Discovery Engine (Hafta 5-7)
- Query generator (LLM ile çok dilli sorgu üretme opsiyonu — sadece query için AI kullanılır, MVP kapsamında OK)
- Google Places connector
- SerpAPI connector
- Bing connector
- Playwright bazlı dizin scraper framework (Kompass, Europages, AYSAD)
- Deduplication engine (fuzzy match: firma adı + domain)
- Discovery job scheduler (Celery)

### Faz 4 — Enrichment (Hafta 8)
- Phone normalization (libphonenumber)
- Website scraper (contact page detection)
- WhatsApp existence check (Cloud API)
- Sector fit scoring (keyword density)
- Lead priority calculation

### Faz 5 — Compliance Engine (Hafta 9)
- Opt-out list management
- Cooldown enforcement
- Quiet hours (timezone-aware)
- IYS API entegrasyonu (TR için)
- GDPR opt-in tracking (EU için)
- Blacklist (manuel + otomatik)
- Full audit log

### Faz 6 — WhatsApp Outreach (Hafta 10-12)
- Template registry (Meta ile senkron)
- Template designer UI (approval submission)
- Sender worker (rate-limited, warm-up algoritması)
- Webhook receiver (delivered/read/failed/incoming)
- STOP/DUR/UNSUBSCRIBE detection
- Campaign scheduler
- Multi-sender load balancing

### Faz 7 — CRM Inbox (Hafta 13-14)
- Realtime conversation list
- Message thread UI
- Assignment (round-robin + manual)
- Quick reply library
- Lead detail sidebar
- Status transitions
- Note & internal comment

### Faz 8 — Raporlama (Hafta 15)
- Kampanya funnel: discovered → contacted → replied → won
- Sender health dashboard
- Compliance report
- Export (CSV)

### Faz 9 — Polish & Beta (Hafta 16)
- E2E test coverage
- Load testing
- Security audit (OWASP Top 10)
- Onboarding wizard
- Dokümantasyon

### Faz 10 — Pilot Launch
- Asansör kasnağı ile canlı test
- Metrik ölçümü
- Iterasyon

---

## 10. Klasör Yapısı

```
leadpulse/
├── apps/
│   ├── api/                    # FastAPI monolith (bounded contexts)
│   │   ├── src/
│   │   │   ├── core/           # config, db, auth, tenancy
│   │   │   ├── modules/
│   │   │   │   ├── sectors/
│   │   │   │   ├── discovery/
│   │   │   │   ├── enrichment/
│   │   │   │   ├── compliance/
│   │   │   │   ├── outreach/
│   │   │   │   ├── inbox/
│   │   │   │   ├── campaigns/
│   │   │   │   └── reporting/
│   │   │   ├── workers/        # Celery tasks
│   │   │   ├── integrations/   # WA, Google, SerpAPI, Bing, IYS
│   │   │   └── main.py
│   │   ├── alembic/
│   │   ├── tests/
│   │   ├── pyproject.toml
│   │   └── Dockerfile
│   │
│   └── web/                    # Next.js
│       ├── src/
│       │   ├── app/            # App Router
│       │   │   ├── [locale]/
│       │   │   │   ├── (auth)/
│       │   │   │   ├── (dashboard)/
│       │   │   │   │   ├── sectors/
│       │   │   │   │   ├── campaigns/
│       │   │   │   │   ├── leads/
│       │   │   │   │   ├── inbox/
│       │   │   │   │   ├── templates/
│       │   │   │   │   └── reports/
│       │   │   │   └── (settings)/
│       │   ├── components/
│       │   ├── lib/
│       │   ├── hooks/
│       │   └── i18n/
│       ├── messages/           # tr.json, en.json, de.json, ar.json, ru.json
│       ├── package.json
│       └── Dockerfile
│
├── packages/
│   └── shared/                 # Ortak tipler (OpenAPI'den generate)
│
├── infra/
│   ├── docker-compose.yml      # dev
│   ├── docker-compose.prod.yml
│   ├── caddy/Caddyfile
│   └── deploy/                 # deployment scripts
│
├── docs/
│   ├── architecture.md
│   ├── api.md
│   ├── compliance.md
│   └── runbook.md
│
└── .github/workflows/
```

---

## 11. Güvenlik & Uyumluluk Kontrol Listesi

- [ ] Tüm endpoint'lerde tenant isolation (RLS + middleware)
- [ ] JWT + refresh token rotation
- [ ] Rate limiting (per-tenant + per-IP)
- [ ] Input validation (Pydantic)
- [ ] SQL injection koruması (ORM parameterized queries)
- [ ] XSS koruması (React default + CSP header)
- [ ] CSRF token (form submissions)
- [ ] Secret'lar `.env` + Docker secrets (kod içinde asla)
- [ ] HTTPS zorunlu (Caddy auto-SSL)
- [ ] KVKK: Aydınlatma metni, VERBIS kaydı, veri sahibi başvuru akışı
- [ ] GDPR: DPA, right to erasure endpoint, data export
- [ ] Audit log: kim, ne, ne zaman, hangi IP
- [ ] Encrypted at rest (Postgres + backup encryption)
- [ ] IYS entegrasyonu (TR ticari ileti)
- [ ] WhatsApp opt-out otomatik işleme
- [ ] Backup: günlük snapshot + haftalık off-site

---

## 12. Bütçe Tahmini (Aylık)

| Kalem | Maliyet |
|---|---|
| Hetzner CX32 (app) | €7 |
| Hetzner CPX21 (db) | €10 |
| Backup storage | €5 |
| Domain + Cloudflare | €2 |
| Google Places API | ~$50 (10K request) |
| SerpAPI | $75 (5K search) |
| Bing Search API | $30 |
| WhatsApp konuşma ücreti | $200–800 (ülkeye göre) |
| Sentry (Team) | $26 |
| **TOPLAM** | **~$400–1000** |

Bütçe (500–2000 USD) içinde rahat kalır.

---

## 13. Riskler & Azaltma

| Risk | Etki | Azaltma |
|---|---|---|
| WhatsApp template reddi | Yüksek | 3 varyant hazırla, "utility" kategorisi tercih et |
| Sender number banı | Yüksek | Warm-up algoritması, multi-sender pool |
| Google Places kotasının aşılması | Orta | Cache + fallback SerpAPI |
| KVKK şikayeti | Yüksek | Sadece kurumsal santral, IYS check, hızlı opt-out |
| Scraping IP ban | Orta | Rotating proxy, Playwright + human-like delay |
| Multi-tenant veri sızıntısı | Kritik | RLS + testler + code review |

---

## 14. Test Stratejisi

- **Unit:** pytest (API), vitest (web) — %70+ coverage
- **Integration:** Testcontainers (Postgres + Redis)
- **Contract:** OpenAPI schema testleri
- **E2E:** Playwright (kritik user journeys)
- **Load:** k6 (1000 concurrent conversations)
- **Security:** OWASP ZAP CI scan

---

## 15. İzleme & Operasyon

- **Metrikler:** Prometheus + Grafana
  - Mesaj gönderim başarı oranı
  - API latency (p50/p95/p99)
  - Celery queue depth
  - WA webhook lag
- **Loglar:** Loki (structured JSON)
- **Alertler:** Grafana → Telegram/Slack
- **Runbook:** `docs/runbook.md` (incident response)

---

## 16. Aşamalı Teslim Planı (İlk 4 Ay)

| Ay | Teslim |
|---|---|
| **1** | Auth + Sector + Discovery MVP (manuel test) |
| **2** | Enrichment + Compliance + WhatsApp send |
| **3** | Inbox + Kampanya + Raporlama |
| **4** | Pilot launch (asansör kasnağı) + iterasyon |

---

## 17. Sonraki Adımlar

1. Bu planı onayla veya değişiklik iste.
2. `BUILD_PROMPT.md` dosyasındaki prompt'u Copilot/Claude'a ver.
3. Faz 0'dan başla: repo kurulumu.
4. Her faz sonunda demo + retro.
