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
| Bilgi grafı | FalkorDB | GraphRAG indeksi (vektör + Türkçe full-text + graf) ve müşteri hafıza grafiği — bkz. ADR-002 |
| Storage | (opsiyonel S3) | Ek doküman / medya için yer tutucu |

## Bounded Context'ler

- **auth** — Tenant, User, Invitation, RefreshToken (rotasyon + soy zinciri)
- **sectors** — Sector + Keyword/Customer/Country/MessageAngle + `presets.py` (asansör kasnağı hazır)
- **discovery** — Campaign, Lead, LeadContact, LeadSource, LeadEnrichment; query üretici + fuzzy dedup
- **compliance** — OptOut, ComplianceCheck, AuditLog; her outbound mesajın geçtiği kapı
- **outreach** — MessageTemplate, SenderProfile, OutreachJob, Conversation, Message + WA webhook
- **reports** — Funnel, sender health, sales performance, per-source precision
- **agents** — Agent + AgentVersion (draft/testing/live/archived, immutable history); Phase E1 scaffold, bkz. aşağı

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

## Agents (Phase E1/E2)

`src/modules/agents/` — TODO #1'in ("her müşterinin adminden WhatsApp üzerinden kendi agent'ını kendisinin geliştirmesi") ilk parçası: tenant başına agent kimliği + versiyonlanmış konfigürasyon + agent builder bot konuşma mekaniği. Canlı otomatik cevap runtime'ı (E3) **henüz yok** — bkz. aşağı.

- **`Agent`** — tenant başına kararlı kimlik (isim, slug, opsiyonel sector bağlantısı).
- **`AgentVersion`** — persona/tone, dil listesi, ürün bilgisi, qualification soruları, guardrails (yasaklı konular, escalation kuralları), reply policies. `status`: `draft → testing → live → archived`.
- **Universal Company Config foundation** — `AgentVersion.company_config`, bir sektöre değil şirket uzayına ait tipli bir grafik olan `CompanyAgentConfig` JSONB belgesini saklar: organization, parties, offerings, relationships, facts, customer-profile tanımları, süreç FSM'leri, politikalar ve agent reply policy. Çekirdek şema kapalıdır; gelecek sektör ayrıntıları yalnızca isimlendirilmiş/sürümlü module sınırından eklenir. Yeni agent için geçerli ama boş bir `draft` envelope oluşturulur; config draft ve rollback ile birlikte klonlanır. Legacy alanlar, runtime bu modele taşınana kadar geriye uyumluluk için kalır. Tam şema manager için `GET /api/v1/agents/company-config/schema` ile alınabilir; ayrıntı için `docs/company-agent-config.md`.
- **Versiyon geçmişi asla silinmez/üzerine yazılmaz.** Draft düzenleme sadece o an DRAFT olan satırı değiştirir; promote sadece status bayrağını taşır; **rollback eski bir versiyonun içeriğini YENİ bir versiyona kopyalayıp onu live yapar** — eski satırı diriltmez. Bu yüzden "ne zaman rollback yapıldı" bilgisi de geçmişte kalıcı olarak görünür kalır (`rolled_back_from_version` alanı).
- Her state-değiştiren aksiyon (`create_agent`, `create_draft`, `update_draft`, `promote_to_testing`, `promote_to_live`, `rollback`) mevcut genel `compliance.models.AuditLog` tablosuna yazar — ayrı bir audit mekanizması kurulmadı.
- Aynı anda agent başına en fazla bir DRAFT ve bir LIVE versiyon — uygulama katmanında zorlanıyor (DB constraint değil).

### Agent builder bot (E2) — mekanik hazır, canlı değil

`BuilderSession` (`agent_builder_sessions` tablosu) bir tenant'ın builder bot ile devam eden konuşmasını tutar: hangi draft'a bağlı, tam transcript (`messages` JSONB), `active/completed/abandoned` status.

- `src/integrations/llm.py` — `LLMClient` Protocol, `OllamaLLMClient` ve
  `ChatCompletionsLLMClient` ile kendi yönettiğimiz model sunucularına bağlanır.
  `NullLLMClient`, yapılandırma yokken sessizce boş/uydurma cevap DÖNMEZ,
  `LLMNotConfiguredError` fırlatır.
- `src/modules/agents/builder.py` — LLM'e ne sorulacağını (`build_system_prompt`, mevcut draft state'i de içerir ki aynı soruyu tekrar sormasın) ve LLM'in JSON cevabının nasıl doğrulanacağını (`parse_llm_response`) tanımlar. LLM'den beklenen format: `{"reply": str, "draft_patch": {...}|null, "ready_to_promote": bool}` — format dışı/bozuk çıktı sessizce geçirilmez, `BuilderResponseParseError` fırlatır (LLM'ler talimatı görmezden gelebilir, bu gerçek bir ihtimal).
- `src/modules/agents/builder_service.py` — `AgentBuilderService`: session açar (mevcut draft'ı kullanır ya da `AgentService.create_draft` ile yenisini açar), her mesajda **tüm transcript'i** LLM'e tekrar gönderir, `draft_patch`'i `AgentService.update_draft` üzerinden uygular (E1'deki aynı yazma yolu — ayrı bir yazma mekanizması yok). `LLMNotConfiguredError` → 503, `BuilderResponseParseError` → 502 — ikisi de temiz bir hata döner, çökme değil.
- Router: `POST /agents/{id}/builder/sessions`, `POST /agents/{id}/builder/sessions/{session_id}/messages` — roadmap'in "admin panelden başlatılan" kısmına karşılık gelen yüzey.
- **Gerçek bir LLM olmadan doğrulandı:** 14 saf parser testi (geçerli/fenced/bozuk JSON, bilinmeyen alanlar, yanlış tipler) + kurgulanmış (scripted) bir stub `LLMClient` ile 6 DB-backed servis testi — session açma, patch uygulama, çok turlu transcript replay, inaktif session'a mesaj (conflict), ve iki hata yolu da (`tests/test_agent_builder_parser.py`, `tests/test_agent_builder_service.py`).

**Canlı değil, bir ayrı sebepten:** **WhatsApp inbound routing yok.** Yukarıdaki
endpoint'ler admin panel yüzeyi; gelen bir WhatsApp mesajının builder-bot
session'ına mı, normal outreach conversation'ına mı, yoksa (E3 gelince) canlı
runtime agent'a mı gideceğine karar veren bir yönlendirme henüz yok — bu ayrı
bir ürün kararı. Model sunucusu ayarları için
[model sunucusu rehberine](./model-sunucusu-rehberi.md) bakın.

**Henüz yapılmadı (E3):**
- Canlı runtime: LIVE versiyonun inbound mesajlara otomatik cevap vermesi, düşük güvenilirlikte insana devretme (Inbox assignment) — yok.
- Kullanım/maliyet metering (billing için ön koşul) — yok.
- Prompt-injection sertleştirmesi, agent cevaplarının compliance filtresinden geçmesi — henüz yapılmadı; runtime yazılmadan önce bu güvenlik incelemesi ayrıca ele alınmalı.

## GraphRAG retrieval ve müşteri hafızası (ADR-002)

Runtime'ın "model seçer, sunucu render eder" sözleşmesi değişmeden, aday fact seçimi
FalkorDB üzerinde hibrit retrieval'a taşındı: `exact` (ürün/rulman kodları, ölçüler) +
`vector` (BGE-M3, Ollama) + `fulltext` (RediSearch Türkçe stemmer) + `graph`
(konu çapaları ve `is_variant_of`/`part_of` soy zinciri) → RRF → `bge-reranker-v2-m3`.
Her müşteri turundan sonra `knowledge` kuyruğundaki worker, onaylı sözlükle kısıtlanmış
bir hafıza özetini `mem_<tenant>` grafiğine yazar; hafıza yalnızca karar girdisidir.
`KNOWLEDGE_BACKEND=lexical` ile tamamen kapatılır. Ayrıntı, güven sınırları ve ölçümler:
[ADR-002](adr/ADR-002-falkordb-graphrag-retrieval.md).

```
inbound → worker → [retriever: FalkorDB kb_<tenant>_<version>] → aday fact ID'leri
        → Qwen3 (action + fact_ids) → literal customer_text → Meta POST
        → (commit sonrası) knowledge kuyruğu → Qwen3 yapısal özet → mem_<tenant>
```

## NIM harness ajanları ve veri sınırı (ADR-004)

Guardrail (jailbreak + içerik güvenliği + konu kontrolü), taranmış PDF için OCR/tablo zinciri,
ürün görseli doğrulama, NIM embedding/rerank adaptörleri ve rol bazlı LLM seçimi eklendi.
Hiçbiri karar vermez veya müşteriye metin yazmaz; hepsi `Settings` ile kapalı başlar ve yalnız
operatörün kendi GPU sunucusundaki konteynerlere bağlanır (`NIM_PUBLIC_HOST_DENYLIST`,
`runtime_preflight.py --require-nim`). Ayrıntı: [ADR-004](adr/ADR-004-nim-harness-agents-and-data-boundary.md),
plan: [nvidia-nim-harness-agents-plan-2026-09-16.md](nvidia-nim-harness-agents-plan-2026-09-16.md).

```
inbound → worker → [guardrail: block → onaylı DECLINE | unavailable(closed) → güvenli tur]
        → retriever → Qwen3 (customer) → literal / [generation rolü] → denetim → Meta POST
ingest  → extract (+ OCR: page-elements → OCR / table-structure) → [guardrail chunk] → chunk
        → embed (ollama|nim, profil parmak izi) → Qwen3/Nemotron (extraction) → aday → publisher
        → görseller → [vision: ret/override/alt] → knowledge_media.verification
```

## Deployment

- Docker Compose ile lokal (Postgres 16, Redis 7, Caddy)
- Prod: Hetzner VPS + Docker Swarm/Compose + Caddy (otomatik TLS)
- `infra/caddy/Caddyfile` — API + Web + webhook reverse proxy
