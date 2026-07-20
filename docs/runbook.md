# Runbook — LeadPulse

## Yerel geliştirme başlatma (macOS)

Ön koşullar: Docker Desktop, Python 3.12 (`brew install python@3.12`), Poetry (`brew install poetry`), Node 20, pnpm.

```bash
# 1. Altyapı (Postgres + Redis)
docker compose up -d postgres redis

# 2. API
cd apps/api
cp .env.example .env
poetry install
poetry run alembic upgrade head
poetry run uvicorn src.main:app --reload --port 8000

# 3. Celery worker (yeni terminal)
cd apps/api
poetry run celery -A src.core.celery_app.celery_app worker -l info

# 4. Celery beat (yeni terminal)
cd apps/api
poetry run celery -A src.core.celery_app.celery_app beat -l info

# 5. Web
cd apps/web
cp .env.example .env.local
pnpm install
pnpm dev
```

Erişim: <http://localhost:3000> (Web), <http://localhost:8000/docs> (API Swagger).

## İlk kurulum akışı

1. Web → `/tr/register` — şirketinizi oluşturun (tenant + owner user).
2. Sectors → **Import elevator sheave preset** — hazır sektör tanımı yüklenir.
3. Campaigns → New campaign — sektörü seçip kampanya adı verin.
4. Campaign satırında **Discover** → Celery worker aday şirketleri arar ve enrich eder.
5. Templates → mesaj taslağı oluşturun → Submit → (Meta onayından sonra) Mark approved.
6. Senders → WhatsApp `phone_number_id`'nizi ekleyin.
7. Meta Developer Console → webhook URL: `https://<domain>/webhooks/whatsapp/<tenant_slug>`, verify token: `.env` içindeki `WHATSAPP_VERIFY_TOKEN`.

## Prod dağıtımı (Hetzner VPS)

```bash
# VPS'de
git clone <repo>
cd leadpulse
cp apps/api/.env.example apps/api/.env  # → gerçek secretları doldur
docker compose -f docker-compose.yml -f infra/docker-compose.yml up -d --build
docker compose exec api alembic upgrade head
```

Caddy otomatik Let's Encrypt sertifikası alır. Firewall'da 80/443 açık olmalı.

**Zorunlu prod secret'ları (`.env`'de gerçek değerlerle değiştirilmeli):** `POSTGRES_PASSWORD` (superuser, sadece migration için), `LEADPULSE_APP_DB_PASSWORD` (API/worker'ın bağlandığı kısıtlı `leadpulse_app` rolü — bkz. `docs/architecture.md` "Multi-tenant izolasyon"). İkisi de `.env.example`'daki dev placeholder değerleriyle prod'a çıkılmamalı; `DATABASE_URL` ve `MIGRATIONS_DATABASE_URL` bu şifrelerle tutarlı olmalı.

## Sık karşılaşılan sorunlar

### `alembic upgrade head` başarısız — enum zaten var
`0001_initial` migration `checkfirst=True` ile idempotent. Yine de sıkışırsa: `psql` içinde `DROP TYPE IF EXISTS <enum_name> CASCADE`.

### Celery worker task'i bulamıyor
`celery_app.include` listesinde modül yolları doğru olmalı. `src.workers.discovery` gibi.

### WhatsApp webhook 403 dönüyor
`WHATSAPP_APP_SECRET` doğru mu? `X-Hub-Signature-256` doğrulaması başarısız oluyor demektir.

### Opt-out otomatik açılmadı
Inbound mesajın metni regex ile eşleşmiyor olabilir. `webhooks._OPT_OUT_RE` regex'ini gözden geçir.

## İzleme

- `docker compose logs -f api`
- Sentry DSN'i `.env`'e koy — otomatik hata gönderilir.
- `POST /api/v1/reports/senders` → sender kalite/tier durumu (dashboard'dan da izlenir).

## Yedekleme

```bash
docker compose exec postgres pg_dump -U leadpulse leadpulse | gzip > backup_$(date +%F).sql.gz
```

Redis'i yedeklemeye gerek yok (broker + cache).
