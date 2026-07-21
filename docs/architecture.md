# Mimari — LeadPulse

## Genel Bakış
Multi-tenant SaaS. Sektör bazlı B2B potansiyel müşteri keşfi + WhatsApp Business Cloud API ile outreach + compliance-first akış.

```
┌──────────────┐    HTTPS     ┌──────────────┐   Redis/Postgres
│  Web (Next)  │ ───────────▶ │  API (FastAPI)│ ────────────────┐
└──────────────┘              └──────┬───────┘                  │
                                     │                           ▼
                                     │              ┌────────────────────────┐
                                     │              │  Celery workers         │
                                     │              │  (discovery, enrich,    │
                                     │              │   outreach, maintenance)│
                                     │              └───────┬────────────────┘
                                     │                      │
                                     │              ┌───────▼────────┐
                                     │              │ WhatsApp Cloud │
                                     │              │  (Meta Graph)  │
                                     │              └────────────────┘
                                     ▼
                              ┌───────────────┐
                              │  Public Web    │  ⇐ Meta Webhooks
                              │  hook receiver │
                              └───────────────┘
```

## Katmanlar

| Katman | Teknoloji | Sorumluluk |
|--------|-----------|------------|
| Web    | Next.js 15 / React 19 / TanStack Query / Tailwind / next-intl | Kullanıcı arayüzü, 5 dil (tr/en/de/ar/ru — ar RTL) |
| API    | FastAPI + SQLAlchemy 2 async + Pydantic v2 | REST endpoints, RBAC, Compliance gate |
| Worker | Celery + Redis | Discovery / Enrichment / Outreach dispatch / Maintenance |
| DB     | PostgreSQL 16 + Row-Level Security | Tenant-isolated veri |
| Cache/broker | Redis 7 | Celery broker, token buckets, IYS cache |
| Storage | (opsiyonel S3) | Ek doküman / medya için yer tutucu |

## Bounded Context'ler

- **auth** — Tenant, User, Invitation, RefreshToken (rotasyon + soy zinciri)
- **sectors** — Sector + Keyword/Customer/Country/MessageAngle + `presets.py` (asansör kasnağı hazır)
- **discovery** — Campaign, Lead, LeadContact, LeadSource, LeadEnrichment; query üretici + fuzzy dedup
- **compliance** — OptOut, ComplianceCheck, AuditLog; her outbound mesajın geçtiği kapı
- **outreach** — MessageTemplate, SenderProfile, OutreachJob, Conversation, Message + WA webhook
- **reports** — Funnel, sender health, sales performance

## Multi-tenant izolasyon (defense in depth)

1. Her tenant-scoped satır `tenant_id UUID NOT NULL` kolonuna sahip.
2. FastAPI dependency (`get_current_claims`) JWT'den `tid`'yi çıkarır, request.state'e yazar.
3. `get_db()` her istek başlangıcında `set_tenant_context()` çağırıp `SELECT set_config('app.current_tenant', :tid, true)` yapar.
4. Migration 0001'de tüm tenant-scoped tabloların üstünde `ENABLE ROW LEVEL SECURITY` + `POLICY tenant_isolation USING (tenant_id::text = current_setting('app.current_tenant', true))`.
5. Servis katmanında `.where(Model.tenant_id == tenant_id)` her zaman uygulanır (belt & suspenders).
6. **İki ayrı DB rolü.** Postgres, superuser bağlantılarında row security'yi hiç uygulamaz (`FORCE ROW LEVEL SECURITY` bile bunu değiştirmez). Bu yüzden:
   - **`leadpulse`** (Postgres init image'ının ürettiği superuser) **sadece** migration çalıştırmak için kullanılır — `MIGRATIONS_DATABASE_URL` (bkz. `Settings.migrations_database_url`, `alembic/env.py`).
   - **`leadpulse_app`** (`NOSUPERUSER NOBYPASSRLS`, migration `0ba5bf6fe643_create_restricted_app_role` ile oluşturulur, sadece `SELECT/INSERT/UPDATE/DELETE` + sequence `USAGE` yetkisine sahip) API/worker süreçlerinin bağlandığı roldür — `DATABASE_URL`.
   - `apps/api/tests/test_rls_isolation.py`, izolasyonun gerçekten `leadpulse_app` ile tutulduğunu (ve superuser ile bilerek başarısız olduğunu) doğrular.

## Auth akışı

- **Access token**: 15 dk TTL, HS256, claims: `sub` (user_id), `tid` (tenant_id), `role`.
- **Refresh token**: 7 gün TTL, SHA-256 hash'li DB'de saklanır, rotasyon zinciri (`replaced_by_id`).
- **RBAC**: `Role` StrEnum, dep factory `require_role(min_role)`, `RequireAgent/Manager/Owner/SuperAdmin` annotated tipler.

## Discovery akışı

1. `POST /api/v1/campaigns/{id}/discover` — status → DISCOVERING, Celery task fırlatır.
2. Worker sektörün `keywords × customers × contact_qualifiers × country` kombinasyonlarını üretir.
3. Connector'lar (Google Places, SerpAPI, Bing, Overpass, ve opsiyonel olarak Web Crawler — bkz. aşağı) paralel çağrılır; hepsi rate-limited (Redis token bucket).
4. Sonuçlar `DiscoveryService.ingest_raw_leads()` üzerinden fuzzy dedup ile Lead tablosuna yazılır (normalized_name + domain).
5. Campaign status → READY; ardından enrichment task'i chain edilir.

## Web crawler (Phase D)

`src/integrations/web_crawler.py` — Playwright tabanlı, jenerik (site'a özel değil) bir crawler connector. `ROADMAP.md`'nin Phase D bölümündeki TODO #2'nin ("anahtar keyword'ler sayesinde web scraping") ilk iskeleti.

- **Varsayılan olarak kapalı** (`WEB_CRAWL_ENABLED=false`) — diğer connector'ların aksine gerçek bir headless browser ile üçüncü parti sitelere gidiyor, bu yüzden deployment başına açık bir onay gerekiyor.
- **robots.txt her fetch'ten önce kontrol edilir** (`src/core/robots.py`), sabit değil — bir site crawl'a izin vermiyorsa asla crawl edilmez. Bu ROADMAP.md'deki "zero robots.txt violations" şartı için tasarım seviyesinde bir garanti, sadece "best effort" değil.
- **Kompass ve Europages hardcode edilmedi.** Orijinal roadmap bu iki dizini örnek olarak veriyordu, ama gerçek robots.txt'leri kontrol edildiğinde: Europages `User-agent: *` için `Disallow: /` (jenerik crawler'lara tamamen kapalı), Kompass ise `/c/` (şirket profili path'i) ve `/search*`'ü disallow ediyor — yani asıl değerli sayfalar zaten engelli. İkisini de hardcode etmek ya robots.txt ihlali ya da işe yaramaz bir scrape anlamına gelirdi. Bunun yerine `WEB_CRAWL_SEED_URLS` operatör tarafından, crawl'a izin verdiği doğrulanmış sitelerle konfigüre edilir.
- Rate limiting diğer connector'larla aynı `TokenBucket` + insan-benzeri rastgele gecikme (1–3s).
- `query_generator.generate_queries(..., include_web_crawl=...)` parametresiyle kapalıyken sorgu bütçesini (`per_country_limit`) tüketmez — kapalı olduğunda diğer connector'ların davranışında hiçbir değişiklik olmaz (bkz. `tests/test_query_generator.py`).

**Henüz yapılmadı / operatör kararı bekliyor (bilerek scaffold dışı bırakıldı):**
- Proxy rotasyonu için gerçek bir proxy sağlayıcı hesabı (`WEB_CRAWL_PROXIES` config'i hazır, ama boş).
- Quality dashboard'un web UI'ı (`GET /api/v1/reports/source-precision` endpoint'i var, bkz. `ReportService.source_precision` docstring'indeki önemli kısıtlama — enrichment eşik-altı Lead satırlarını fiziksel olarak sildiği için gerçek "precision" ölçümü şu an mimari olarak mümkün değil).
- Celery worker autoscaling (Phase B'nin deployment altyapısı henüz yok).

## Enrichment akışı

- Website fetch + strip HTML → keyword-hit tabanlı fit skoru (0–100).
- `phonenumbers` ile E.164 normalizasyon.
- Fit ≥ 80 → QUALIFIED (≥ 90 → priority=HIGH, aksi halde MEDIUM); < 80 → DISCARDED, ardından fiziksel olarak silinir (`workers/enrichment.py::_enrich_campaign`).

## Outreach akışı

Her dakika `dispatch_outreach` çalışır:

1. PENDING / DEFERRED (past due) jobları çek.
2. Compliance gate: opt-out, cooldown (30d), quiet hours (09:00–18:00 lokal), IYS (TR).
3. Sender seç (aktif + healthy + `daily_sent < daily_cap`, en boş olan).
4. WhatsApp template message gönder → Job status güncelle, Conversation + Message log'la.
5. Webhook (`POST /webhooks/whatsapp/{tenant_slug}`) status güncellemeleri ve inbound mesajları işler; STOP-benzeri kelimeleri regex ile yakalayıp otomatik opt-out açar.

## Deployment

- Docker Compose ile lokal (Postgres 16, Redis 7, Caddy)
- Prod: Hetzner VPS + Docker Swarm/Compose + Caddy (otomatik TLS)
- `infra/caddy/Caddyfile` — API + Web + webhook reverse proxy
