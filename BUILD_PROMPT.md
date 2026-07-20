# AI Geliştirme Promptu — LeadPulse

Bu dosya, projeyi baştan sona AI (Copilot / Claude / GPT) ile yaptırırken kullanacağın **master prompt**'tur. Her yeni faza başlarken ilgili bölümü kopyalayıp AI'a ver. AI'a hep aynı bağlamı vermek için önce **"SİSTEM PROMPTU"** kısmını yerleştir.

---

## 🎯 SİSTEM PROMPTU (Her yeni AI sohbetinin başında ver)

```
Sen bir kıdemli full-stack yazılım mimarı ve geliştiricisin. Aşağıdaki projede bana yardım edeceksin. Kural olarak:

1. Kodu her zaman production-quality yaz. Type-safe, test edilebilir, temiz.
2. Türkçe konuş, ama kod, değişken, commit mesajları ve dokümantasyon İngilizce olsun.
3. Güvenlik önce gelir. OWASP Top 10'a karşı savun. Multi-tenant izolasyonu asla ihlal etme.
4. KVKK ve GDPR uyumluluğu tasarımın parçası. Her kullanıcı verisi işleminde audit log tut.
5. Önce plan sun, onay bekle, sonra kod yaz. Kod yazarken dosya yolunu belirt.
6. Belirsizlik varsa VARSAYIM yapma, bana sor.
7. Her yeni özellikte: modelleri, migration'ı, servis katmanını, endpoint'i, testi ve UI'ı bir arada teslim et.

PROJE: LeadPulse

Kısa tanım:
Multi-tenant SaaS. Kullanıcı sektör seçer (örn. asansör, mobilya, CNC yedek parça).
Sistem o sektöre uygun B2B firmaları Google Places, SerpAPI, Bing ve sektör dizinlerinden
otomatik bulur, telefonları normalize edip WhatsApp uygunluğunu doğrular,
compliance (KVKK/GDPR/IYS/opt-out/quiet hours) filtresinden geçirir ve
WhatsApp Business Cloud API üzerinden onaylı template mesajlarla toplu outreach yapar.
Gelen cevaplar CRM inbox'ta yönetilir.

İlk pilot: Asansör kasnağı satışı. Hedef ülkeler: TR, DE, UK, UAE, KSA, RU.
Diller: TR, EN, DE, AR, RU.

Teknoloji stack (değiştirme):
- Backend: Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2 + Celery + Redis
- Frontend: Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query + next-intl
- DB: PostgreSQL 16 (Row-Level Security ile tenant izolasyonu)
- Infra: Docker Compose, Hetzner VPS, Caddy reverse proxy, GitHub Actions CI
- External: Meta WhatsApp Business Cloud API, Google Places, SerpAPI, Bing, IYS

Repo yapısı (monorepo):
leadpulse/
  apps/api/        (FastAPI)
  apps/web/        (Next.js)
  packages/shared/ (paylaşılan tipler)
  infra/           (docker-compose, Caddy)
  docs/

Modül sınırları (bounded contexts):
sectors, discovery, enrichment, compliance, outreach, inbox, campaigns, reporting, auth

Tasarım prensipleri:
- Her modül kendi router + service + repository + schema + tests klasörüne sahip.
- Tenant isolation: her tabloda tenant_id + Postgres RLS policy.
- Async her yerde (async def, asyncpg, httpx).
- Celery worker'lar background job için (discovery, enrichment, outreach).
- WhatsApp gönderimi rate-limited (warmup algoritması).
- Tüm dış API çağrıları circuit breaker + retry (tenacity).
- Loglar structured JSON (structlog).
- Config: pydantic-settings + .env.
- Test: pytest + pytest-asyncio + testcontainers + vitest + Playwright.

Ben "faz X" dediğimde ilgili fazın kodunu üret.
```

---

## FAZ 0 — Repo & Altyapı Kurulumu

```
Faz 0'ı başlatalım. Şunları yap:

1. Monorepo iskeletini oluştur:
   - /apps/api    (Python 3.12 + FastAPI + Poetry)
   - /apps/web    (Next.js 15 + pnpm + TypeScript)
   - /packages/shared
   - /infra/docker-compose.yml (postgres:16, redis:7, mailhog)
   - /infra/caddy/Caddyfile
   - /.github/workflows/ci.yml (lint + test + build, hem api hem web)
   - /.editorconfig, /.gitignore, /README.md, /LICENSE (MIT)

2. apps/api için:
   - pyproject.toml: fastapi, uvicorn[standard], sqlalchemy[asyncio], asyncpg,
     alembic, pydantic, pydantic-settings, celery[redis], httpx, tenacity,
     structlog, python-jose[cryptography], passlib[bcrypt], phonenumbers,
     python-multipart. Dev: pytest, pytest-asyncio, ruff, mypy, testcontainers.
   - src/core/{config.py, db.py, logging.py, security.py, tenancy.py}
   - src/main.py: FastAPI app + health endpoint /healthz
   - alembic init
   - Dockerfile (multi-stage)
   - tests/test_health.py

3. apps/web için:
   - Next.js 15 create (App Router, TypeScript, Tailwind, ESLint)
   - shadcn/ui init, next-intl kurulumu, TanStack Query provider
   - /messages/{tr,en,de,ar,ru}.json boş şablon
   - src/app/[locale]/layout.tsx (i18n root)
   - src/app/[locale]/page.tsx (landing placeholder)
   - Dockerfile

4. Root:
   - Makefile: make dev, make test, make lint, make migrate, make up, make down
   - docker-compose.yml (dev): api, web, postgres, redis, mailhog
   - README: kurulum adımları

Kod üret, dosya yollarını belirt, komutları göster. Tüm bunları eksiksiz teslim et.
```

---

## FAZ 1 — Auth & Multi-Tenancy

```
Faz 1: Multi-tenant auth sistemi.

Gereksinimler:
- Tenant tablosu (id, name, slug, plan, wa_business_account_id, status, created_at)
- User tablosu (id, tenant_id, email UNIQUE per tenant, password_hash, role, is_active, locale, timezone, last_login_at)
- Role enum: super_admin, tenant_owner, sales_manager, sales_agent, viewer
- Invitation tablosu (id, tenant_id, email, role, token, expires_at, accepted_at)
- Password: bcrypt hash
- Auth: JWT access (15dk) + refresh (7gün, rotate on use), refresh token DB'de tutulur
- Tenant isolation: her API request'te JWT'den tenant_id çıkarılır, DB session'a `SET LOCAL app.current_tenant` yapılır
- Postgres RLS policy: sadece current_tenant satırları görünür
- Endpoint'ler:
  POST /auth/register-tenant  (yeni tenant + owner user)
  POST /auth/login
  POST /auth/refresh
  POST /auth/logout
  POST /auth/invite  (tenant_owner+)
  POST /auth/accept-invite
  GET  /me
  GET  /users  (list within tenant)
  PATCH /users/{id}  (rol değiştir, deactivate)
- Frontend: /login, /register, /accept-invite/[token], /settings/users sayfaları
- Middleware: protected routes, role check
- Test: unit + integration (testcontainers), auth flow E2E (Playwright)

Alembic migration hazırla. RLS SQL'i migration içine koy.
Sonra endpoint + servis + repository + Pydantic şemalarını üret.
En son frontend sayfalarını üret.
```

---

## FAZ 2 — Sector Configuration

```
Faz 2: Sektör yönetimi.

Modeller:
- sectors (id, tenant_id, name, slug, product_name, description, default_language, is_active)
- sector_keywords (id, sector_id, keyword, language, keyword_type: positive|negative)
- sector_target_customers (id, sector_id, customer_type, language, description)
- sector_countries (id, sector_id, country_code, timezone, priority)
- sector_message_angles (id, sector_id, angle, language)

Endpoint'ler (hepsi tenant-scoped):
- CRUD /sectors
- CRUD /sectors/{id}/keywords
- CRUD /sectors/{id}/target-customers
- CRUD /sectors/{id}/countries
- POST /sectors/{id}/duplicate (kopyala)
- POST /sectors/import (JSON import)
- GET  /sectors/{id}/export (JSON export)

Sektör şablonu preset'leri (seed):
- Asansör kasnağı (TR + EN + DE + AR + RU keyword'ler ile birlikte)

Frontend:
- /sectors listesi (tablo + filtre + arama)
- /sectors/new wizard (4 adım: temel bilgi → keywords → target customers → countries)
- /sectors/[id] detay tablı arayüz (keywords / customers / countries / templates)
- shadcn/ui: DataTable, Form, Dialog, Tabs kullan
- i18n: 5 dil için label'lar

Test: unit + integration.
```

---

## FAZ 3 — Discovery Engine

```
Faz 3: Lead discovery engine.

Bileşenler:
1. Query Generator
   - Input: sector_id, country_code, language
   - Output: List[SearchQuery] (kaynak bazlı)
   - Kural tabanlı: sector_keywords × sector_target_customers × iletişim kelimeleri
   - Örnek: ["asansör bakım firması iletişim", "asansör servisi telefon", ...]
   - Ülke TLD kısıtı: site:.de gibi

2. Connectors (abstract base + implementations):
   - GooglePlacesConnector (text search + details)
   - SerpAPIConnector
   - BingSearchConnector
   - DirectoryScraper (Playwright base class + Kompass, Europages, AYSAD implementasyonları)
   - Her connector: search(query) -> AsyncIterator[RawLead]

3. Orchestrator (Celery task):
   - Input: campaign_id
   - Query generator çalıştır
   - Her connector'a paralel gönder (rate-limited)
   - Deduplication: (normalized_company_name, domain) fuzzy match
   - Sonuçları leads tablosuna yaz (status=discovered)

4. Rate limiting:
   - Per-connector token bucket (Redis)
   - Circuit breaker (tenacity)

5. Modeller:
   - campaigns (id, tenant_id, sector_id, name, status, filters, quotas_daily, created_at)
   - leads (id, tenant_id, sector_id, campaign_id, company_name, website, country, city, source, source_url, status, discovered_at)
   - lead_sources (id, lead_id, source_type, source_url, raw_data_json)

6. Endpoint:
   - POST /campaigns/{id}/discover  (job'ı tetikler)
   - GET  /campaigns/{id}/discovery-status
   - GET  /leads  (filtreli list)

7. Frontend:
   - /campaigns listesi + yeni kampanya wizard'ı
   - /campaigns/[id] canlı progress (SSE veya polling)
   - /leads global lead görünümü

Test: mock connector'lar, deduplication testleri.

DİKKAT: Playwright scraper'lar robots.txt'ye uymalı, User-Agent belirtmeli, request'ler arası jitter (1-3sn) olmalı. IP ban riski için proxy rotation için placeholder bırak.
```

---

## FAZ 4 — Enrichment

```
Faz 4: Lead enrichment.

Adımlar (Celery task chain):
1. Phone normalization
   - libphonenumber ile E.164 formatına çevir
   - Ülke koduna göre parse
   - Geçersizse contact.is_valid=false

2. Website analysis
   - httpx ile fetch (timeout 10s)
   - BeautifulSoup ile contact page bul (/contact, /iletisim, /kontakt vb.)
   - Ek telefon + email topla
   - Sayfadaki metinden sector keyword yoğunluğunu hesapla

3. WhatsApp existence check
   - WA Cloud API `contacts` endpoint (rate-limit çok sıkı, batch ve cache)
   - Sonucu 30 gün cache'le (Redis)

4. Sector fit scoring
   - Formül: (positive_keyword_hits * 10) - (negative_keyword_hits * 20) + website_richness_bonus
   - 0-100'e normalize et
   - fit_score < 30 → status=discarded

5. Priority calculation
   - fit_score + country_priority + has_whatsapp → priority (low/med/high)

Modeller:
- lead_contacts (id, lead_id, type: phone|email, raw_value, e164_value, is_valid, is_whatsapp, wa_checked_at, consent_status)
- lead_enrichment (id, lead_id, fit_score, employees_est, categories, contact_page_url, meta_json, enriched_at)

Endpoint:
- POST /leads/{id}/enrich (manuel tetikle)
- Auto: discovery bittikten sonra chain ile enrichment

Frontend:
- /leads/[id] detay: enrichment durumu, skorlar, iletişim bilgileri
- Bulk enrich action

Test: fixture-based scoring, mock WA API.
```

---

## FAZ 5 — Compliance Engine

```
Faz 5: Compliance gate. Bu KRİTİK bir modül. Her mesaj göndermeden önce buradan geçmeli.

Kurallar:
1. Opt-out check
   - opt_outs tablosunda phone_e164 var mı? Varsa BLOCK
2. Cooldown
   - Bu numaraya son 30 günde mesaj gönderildi mi? Varsa BLOCK
3. Quiet hours
   - Hedef ülke saat diliminde 09:00-18:00 arası mı? Değilse sonraki uygun slot'a ertele
4. Local Sunday/holiday check (opsiyonel, bayrak flag'le)
5. Turkey specific: IYS check
   - IYS API'ye phone gönder, "red" varsa BLOCK
   - Cache 24h
6. EU (GDPR): Opt-in gerekli mi kontrol et (tenant setting)
7. Blacklist (manuel + otomatik)

Modeller:
- opt_outs (id, tenant_id, phone_e164, source: user_reply|manual|iys|gdpr, reason, created_at)
- compliance_checks (id, lead_id, contact_id, check_type, result: pass|block|defer, details_json, checked_at, next_allowed_at)
- audit_logs (id, tenant_id, actor_id, action, entity, entity_id, ip, user_agent, meta_json, created_at)

Service:
- ComplianceService.check(contact) -> ComplianceDecision
  - decision: allowed | blocked | deferred
  - reason: enum
  - next_allowed_at: datetime | None

Endpoint:
- GET  /opt-outs
- POST /opt-outs (manuel ekle)
- DELETE /opt-outs/{id}
- POST /leads/{id}/blacklist
- GET  /compliance/report

Otomatik opt-out:
- WhatsApp webhook'unda gelen mesaj body'sinde "STOP", "DUR", "İSTEMİYORUM", "UNSUBSCRIBE", "STOPP", "الإلغاء", "СТОП" kelimeleri regex ile tespit → otomatik opt-out ekle

Test: her kural için unit test, integration test full pipeline.
```

---

## FAZ 6 — WhatsApp Outreach

```
Faz 6: WhatsApp Business Cloud API entegrasyonu.

Bileşenler:

1. Template Registry
   - Meta Graph API'den template'leri fetch et
   - message_templates tablosuna sync'le
   - Status: PENDING, APPROVED, REJECTED
   - Endpoint: POST /templates (create), POST /templates/{id}/submit (Meta'ya gönder), GET /templates

2. Template Designer UI
   - Header (text/image/document)
   - Body (variable placeholders {{1}}, {{2}})
   - Footer
   - Buttons (quick reply / call-to-action)
   - Preview
   - Submit for approval

3. Sender Worker (Celery)
   - outreach_jobs tablosundan `status=queued AND scheduled_at <= now()` çek
   - Compliance check tekrar yap (defense in depth)
   - WA Cloud API POST /messages
   - wa_message_id kaydet, status=sent
   - Hata: retry with exponential backoff (max 3), sonra failed

4. Rate Limiter & Warmup
   - sender_profiles tablosu
   - Tier'lar: T1 (1K/day), T2 (10K), T3 (100K), T4 (unlimited)
   - Warmup: yeni number ilk gün 20, gün 2: 50, gün 3: 100, ... (Meta rules)
   - Redis token bucket

5. Multi-sender support
   - Bir tenant'ta birden fazla wa_phone_number_id olabilir
   - Round-robin veya lead country'e göre routing

6. Webhook Receiver
   - POST /webhooks/whatsapp/{tenant_slug}
   - Signature verify (X-Hub-Signature-256)
   - Message events: sent, delivered, read, failed → outreach_jobs güncelle
   - Incoming message → conversations + messages tablosuna yaz
   - STOP detection → opt_outs

7. Modeller:
   - message_templates (yukarıda tanımlı)
   - outreach_jobs (id, tenant_id, campaign_id, lead_id, contact_id, template_id, template_variables_json, scheduled_at, status, wa_message_id, error, attempts, sent_at, delivered_at, read_at)
   - sender_profiles (id, tenant_id, wa_phone_number_id, display_name, daily_limit, current_tier, warmup_day, sent_today, last_reset_at, health_score)
   - conversations (id, tenant_id, lead_id, contact_id, sender_profile_id, status: open|closed|snoozed, last_message_at, assigned_user_id)
   - messages (id, conversation_id, wa_message_id, direction: in|out, type: text|image|document|template, body, media_url, status, timestamp)

8. Campaign Scheduler
   - Kampanya başlat → uygun lead'ler için outreach_jobs oluştur
   - Compliance check → uygun slot'a schedule et
   - Sender profile atama

Endpoint:
- POST /campaigns/{id}/start
- POST /campaigns/{id}/pause
- POST /campaigns/{id}/resume
- GET  /senders (health dashboard)

Frontend:
- Template Designer (/templates/new)
- /senders (health, warmup progress)
- /campaigns/[id]/monitor (real-time sending)

Test: mock WA API, webhook signature test, warmup logic, retry.
GÜVENLİK: Webhook signature MUTLAKA doğrula. Verify token dinamik olsun.
```

---

## FAZ 7 — CRM Inbox

```
Faz 7: Realtime CRM inbox.

Özellikler:
1. Conversation list (sol panel)
   - Filtre: assigned to me / unassigned / all / status
   - Arama: firma adı, telefon
   - Sıralama: last_message_at DESC
   - Unread badge

2. Message thread (orta panel)
   - Chronological, chat balonu UI
   - Inbound/outbound ayrım
   - Delivery/read tick'leri
   - Timestamp + status

3. Composer
   - 24-saat session içindeyse serbest text
   - Session dışındaysa sadece template gönderme
   - Quick reply library
   - Attach file (image, PDF)

4. Lead detail sidebar (sağ panel)
   - Firma bilgisi
   - Enrichment skorları
   - Kampanya
   - Status change
   - Notes (internal)
   - Tag ekleme

5. Assignment
   - Manual assign to agent
   - Round-robin (setting)
   - Reassign, transfer

6. Realtime
   - WebSocket (FastAPI + socket.io veya sse-starlette)
   - Yeni mesaj bildirimi (browser Notification API)
   - Typing indicator (opsiyonel)

7. Endpoint:
   - GET  /conversations
   - GET  /conversations/{id}
   - GET  /conversations/{id}/messages
   - POST /conversations/{id}/messages (send)
   - PATCH /conversations/{id} (assign, status, snooze)
   - POST /conversations/{id}/notes

Frontend:
- /inbox 3-column layout (shadcn/ui + Radix)
- Keyboard shortcuts (j/k navigate, r reply, a assign)
- Optimistic updates

Test: WebSocket integration test, message ordering, race conditions.
```

---

## FAZ 8 — Raporlama

```
Faz 8: Raporlama.

Dashboardlar:
1. Campaign Funnel
   - Discovered → Enriched → Qualified → Contacted → Delivered → Read → Replied → Interested → Won
   - Konversiyon oranları
   - Chart: Recharts

2. Sender Health
   - Her sender profile için: sent, delivered, read, failed
   - Warmup progress
   - Health score trend

3. Compliance Report
   - Opt-out sayısı (kaynak bazlı)
   - Blocked mesajlar (sebep bazlı)
   - IYS red oranı

4. Sales Performance (per agent)
   - Konuşma sayısı
   - Response time (ortalama)
   - Kazanılan / kaybedilen

Endpoint:
- GET /reports/campaigns/{id}/funnel
- GET /reports/senders/health
- GET /reports/compliance
- GET /reports/agents/performance
- GET /reports/export?type=csv

Frontend:
- /reports altında tab'lı sayfa
- Date range filter
- CSV export

Test: aggregation query'lerinin doğruluğu.
```

---

## FAZ 9 — Polish, Test, Güvenlik

```
Faz 9: Prodüksiyon hazırlığı.

1. E2E test coverage
   - Playwright: register → create sector → run campaign → check inbox → reply → mark won

2. Load testing
   - k6: 1000 eş zamanlı webhook, 500 concurrent inbox user

3. Security audit
   - OWASP ZAP scan CI'a ekle
   - Dependency scan: safety, npm audit
   - Secrets scan: gitleaks
   - Manuel test: SQL injection, XSS, CSRF, IDOR (multi-tenant sızıntı)

4. Performance
   - N+1 query fix (SQLAlchemy selectinload)
   - DB index review
   - Frontend bundle analyzer

5. Onboarding wizard
   - Yeni tenant register → WA setup → sector wizard → first campaign

6. Documentation
   - docs/architecture.md (Mermaid diagramlar)
   - docs/api.md (OpenAPI'den generate)
   - docs/runbook.md (incident response, restart, backup restore)
   - docs/compliance.md (KVKK/GDPR checklist)

7. Backup & restore
   - Hetzner snapshot günlük
   - pg_dump haftalık off-site (S3)
   - Restore drill
```

---

## FAZ 10 — Pilot Launch (Asansör Kasnağı)

```
Faz 10: Canlı pilot.

1. Production deployment
   - Hetzner CPX21 (db) + CX32 (app)
   - Caddy + auto-SSL
   - Sentry + Loki + Grafana
   - Uptime monitor (uptimerobot)

2. Asansör kasnağı sektör seed'i
   - Keywords (5 dil): asansör bakım, elevator maintenance, Aufzug Wartung, صيانة المصاعد, обслуживание лифтов, ...
   - Target customers: bakım firması, üretici, ithalatçı, servis
   - Countries: TR, DE, UK, UAE, KSA, RU

3. Template hazırla ve Meta'ya onaya gönder
   - Marketing template (5 dilde varyantlar)
   - Utility template (takip)

4. Warmup başlat
   - İlk gün 20 mesaj, 7 gün boyunca artan

5. Metrik ölç
   - Yanıt oranı
   - Template reddedilme oranı
   - Sender health

6. Iterasyon
   - Weekly retro
   - Mesaj varyant A/B testi
```

---

## 🧭 Kullanım Talimatı

1. Her yeni AI chat session'ının BAŞINA "SİSTEM PROMPTU" bölümünü koy.
2. Sonra ilgili fazın prompt'unu ver.
3. AI plan sunarsa onayla, sonra kod istemek için "kodu üret" de.
4. Uzun fazları alt-adımlara böl (örn. Faz 6'yı "template designer", "sender worker", "webhook" olarak 3 sohbette yaptır).
5. Her faz sonunda:
   - `git commit -m "feat(phase-X): ..."`
   - Test çalıştır (`make test`)
   - Kısa demo/screencast al
6. Sorun çıkarsa: log + hata mesajı + ilgili dosya + "bu hatayı çöz" diyerek AI'a dön.

---

## 🚨 KIRMIZI ÇİZGİLER

AI'a hatırlatman gereken kurallar (gerekirse bu bloğu prompt'a ekle):

```
KIRMIZI ÇİZGİLER:
- WhatsApp Web / whatsapp-web.js / Selenium ile WA otomasyonu ASLA yapma. Sadece Cloud API.
- Google arama sonuçlarını doğrudan HTML parse etme. Sadece resmi API'ler (SerpAPI, Places, Bing).
- Tenant isolation ihlali kritik güvenlik açığıdır. Her query'de tenant_id filtresi ZORUNLU.
- Compliance check'i bypass eden endpoint YOK.
- Secret'lar kod içinde YOK.
- Password hash sadece bcrypt / argon2.
- HTTPS olmadan production YOK.
```

---

Hazırsın. Faz 0 ile başla.
