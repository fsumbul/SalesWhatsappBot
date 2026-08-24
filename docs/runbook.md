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

## Taşınabilir model sunucusu kurulumu

Uygulama, modelin Arch Linux'ta veya aynı makinede olmasını gerektirmez.
Docker ile çalışan Ollama, ayrı bir Ollama host'u veya standart chat uyumlu bir model
sunucusu/API (vLLM, LocalAI, llama.cpp, vb.) kullanılabilir. Ortak ayarlar ve
güvenli ağ önerileri için [model sunucusu rehberine](./model-sunucusu-rehberi.md)
bakın. Üretim öncesi kontrolde model erişimini zorunlu kılmak için
`runtime_preflight.py --require-llm` kullanın; eski `--require-ollama` seçeneği
geriye dönük uyumluluk için korunmuştur.

## İlk kurulum akışı

1. Web → `/tr/register` — şirketinizi oluşturun (tenant + owner user).
2. Sectors → **Import elevator sheave preset** — hazır sektör tanımı yüklenir.
3. Campaigns → New campaign — sektörü seçip kampanya adı verin.
4. Campaign satırında **Discover** → Celery worker aday şirketleri arar ve enrich eder.
5. Templates → mesaj taslağı oluşturun → Submit → (Meta onayından sonra) Mark approved.
6. Senders → WhatsApp `phone_number_id`'nizi ekleyin.
7. Meta Developer Console → webhook URL: `https://<domain>/webhooks/whatsapp/<tenant_slug>`, verify token: `.env` içindeki `WHATSAPP_VERIFY_TOKEN`.

## Mevcut prod sunucusu (ashiraai; ortama özel geçmiş kurulum)

Bu proje `ashiraai` adıyla deploy edilmiş durumda. Erişim bilgileri:

| | |
|---|---|
| SSH | `Administrator@94.73.180.208` (Windows, OpenSSH Server, host `ns10A9B32336C58`) |
| Anahtar | `~/.ssh/sefertasi_vm_ed25519` (repoda **yok**, yalnız geliştirme makinesinde) |
| API dizini | `C:\sites\ashiraai\app` (bu reponun `apps/api` içeriği) |
| Gerçek `.env` | `C:\sites\ashiraai\app\.env` |

Aynı sunucuda başka projeler de barınıyor (`C:\sites\sefertasi`, `C:\sites\kleaf`);
bu projeyle çalışırken yalnız `C:\sites\ashiraai` altına dokunulmalıdır.

Bağlantı testi:

```bash
ssh -i ~/.ssh/sefertasi_vm_ed25519 Administrator@94.73.180.208 hostname
```

**Dolu WhatsApp/Meta secretları yalnız sunucudaki `.env` dosyasındadır**; yerel
`apps/api/.env` içindeki `WHATSAPP_*` alanları boştur. Bu değerler repoya, doküman
dosyalarına veya commit mesajlarına asla yazılmamalıdır. WhatsApp gönderimi test
edilecekse ya sunucu üzerinde çalıştırılmalı ya da değerler elle yerel `.env`'e
kopyalanmalıdır (bkz. `apps/api/scripts/send_test_template.py`).

### Artı Kasnak canlı WhatsApp ajanı

Canlı akış aşağıdaki bileşenlerden oluşur:

```text
Meta webhook -> PostgreSQL agent_runtime_jobs -> agent_runtime Celery worker
             -> Windows 127.0.0.1:11434 -> ters SSH tüneli
             -> Arch 127.0.0.1:11434 (Ollama qwen3:8b)
             -> onaylı fact renderer -> WhatsApp Cloud API
```

Ollama hiçbir LAN veya internet arayüzüne açılmaz. Windows'taki port da yalnız
loopback'e bağlanır. Windows üzerinde parola/kabuk erişimi olmayan
`ollama_tunnel` hesabı kullanılır; tünel anahtarı ve known-hosts dosyası repoya
girmez.

Canlı `.env` seçimleri (secret değildir):

```dotenv
APP_ENV=production
WHATSAPP_AGENT_SLUG=arti-kasnak
LLM_PROVIDER=ollama
LLM_MODEL=qwen3:8b
LLM_BASE_URL=http://127.0.0.1:11434/v1
```

Artı Kasnak yapılandırmasını doğrulama ve idempotent yayınlama:

```powershell
cd C:\sites\ashiraai\app
C:\sites\ashiraai\runtime\python312-embed\python.exe scripts\bootstrap_arti_kasnak_agent.py --validate-only
C:\sites\ashiraai\runtime\python312-embed\python.exe scripts\bootstrap_arti_kasnak_agent.py --dry-run
C:\sites\ashiraai\runtime\python312-embed\python.exe scripts\bootstrap_arti_kasnak_agent.py
```

Windows tünel kullanıcısını yeniden uzlaştırmak gerekirse:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\sites\ashiraai\app\ops\windows\provision-ollama-tunnel-user.ps1
```

Arch üzerinde `/home/binc/.ssh/ashiraai_ollama_tunnel_ed25519` ve
`/home/binc/.ssh/ashiraai_ollama_known_hosts` güvenli biçimde yerleştirildikten
sonra kalıcı tüneli kurma:

```bash
cd /home/binc/ashiraai-ollama-tunnel
./install-ollama-tunnel.sh ./ashiraai-ollama-tunnel.service
systemctl is-active ollama.service ashiraai-ollama-tunnel.service
```

Windows görevleri yalnız ajan kuyruğunu çalıştırır; kampanya/discovery
otomasyonunu açmaz:

- `AshiraaiApi`
- `AshiraaiAgentWorker`
- `AshiraaiAgentRecovery`

Agent worker bilerek `--pool=solo --concurrency=1` çalışır. Opt-out gönderim
sınırı iş başına bir ana ve bir guard DB bağlantısı kullanır. Aynı process'te
async/thread/gevent concurrency `16+` yapılmadan önce bu kilit/havuz tasarımı
yeniden değerlendirilmelidir.

Canlı doğrulama:

```powershell
cd C:\sites\ashiraai\app
C:\sites\ashiraai\runtime\python312-embed\python.exe scripts\runtime_preflight.py --tenant-slug kasnak
C:\sites\ashiraai\runtime\python312-embed\python.exe scripts\runtime_preflight.py --tenant-slug kasnak --require-ollama
C:\sites\ashiraai\runtime\python312-embed\python.exe scripts\runtime_webhook_probe.py --tenant-slug kasnak
curl.exe -fsS http://127.0.0.1:8001/health
curl.exe -fsS http://127.0.0.1:11434/api/tags
```

İlk preflight, model çevrimdışıyken yalnız güvenli deterministik fallback ile
çalışabilecek üretim durumunu doğrular. `--require-ollama` ise yapılandırılmış
model etiketi Windows'taki ters tünelden gerçekten görünmeden başarısız olur;
Qwen modu için bu komutun sıfır koduyla bitmesi zorunludur. Preflight ayrıca
Meta kimliklerini ve WABA aboneliğini, kısıtlı DB rolünü, migration head'ini,
güçlü JWT imzalama anahtarını, aktif WABA→tenant tekilliğini, tenant/sender
bağını, canlı ajan sürümünü, Redis/worker/görevleri ve üç örneklik ortalama
Windows CPU yükünün `%90`, tekil tepe değerin de `%98` altında olduğunu kontrol
eder.

`runtime_webhook_probe.py`, gerçek App Secret ile imzalanmış ama var olmayan bir
mesaj durum kimliği taşıyan payload gönderir. Böylece dış HTTPS, imza kontrolü,
tenant/sender bağı ve parser sınanır; müşteri mesajı veya outbound iş oluşturmaz.
İmzasız aynı endpoint her zaman `403` dönmelidir.

Model hatasında ham model metni müşteriye gönderilmez. Sunucu yalnız onaylı
`customer_text` alanlarını render eder; model erişilemiyorsa deterministik
yetkili iletişim bilgisi kullanılır ve konuşma otomatik yanıtlara karşı
duraklatılır. Aktif bir insan kullanıcı/atanmış takip süreci kurulmadan bot,
talebin bir kişiye iletildiğini iddia etmez. Fiyat, stok, kesin teslim tarihi,
sertifika geçerliliği ve güvenlik-kritik nihai ürün seçimi otomatik yanıtlanmaz.

### 2026-08-15 üretim kontrol noktası

- Windows API, ajan worker ve recovery görevleri kurulu ve çalışır durumda
  doğrulandı; migration head `c4f9128ab6d0`.
- `kasnak` tenant'ı ile `arti-kasnak` ajan sürüm 2 canlı/onaylıdır.
- Meta token, telefon kimliği, WABA kimliği ve WABA uygulama aboneliği canlı
  Graph okumasıyla doğrulandı.
- İmzalı dış webhook probe `200`, imzasız webhook `403` verdi.
- `STOP`/`DUR`, model çalışıyor olsa bile henüz gönderilmemiş işleri terminal
  `SKIPPED` durumuna alır; Meta POST sınırı aynı telefon kilidi altında opt-out
  ve tenant bağını yeniden kontrol eder.
- Arch host `192.168.10.39` üzerinden erişildi; Ollama ve kalıcı
  `ashiraai-ollama-tunnel.service` etkin/çalışır durumdadır. Windows loopback
  üzerinden `qwen3:8b` ve `qwen2.5:7b` görüldü. Gerçek üretim uygulama koduyla
  yapılan yapılandırılmış testte Qwen yalnız onaylı fact ID'leri döndürdü,
  sunucu güvenli metni render etti ve fallback kullanılmadı.
- Publio uygulaması sahibi onayıyla tamamen devreden çıkarıldı: dört `publio-*`
  PM2 kaydı, restart fırtınasından kalan süreçler, Windows servisi, IIS sitesi,
  proje ve log dizinleri kaldırıldı. Küçük yapılandırma yedeği erişimi kısıtlı
  `C:\server-backups\publio-removal-20260815-204301` dizinindedir.
- Publio servisinin daha önce dolaylı olarak yönettiği Nimbus, Publio'dan
  bağımsız otomatik başlayan `SharedNodeApps` NSSM servisine taşındı ve port
  `3100` üzerinde `online` doğrulandı. Paylaşılan `C:\publio-pm2` veri dizini
  Nimbus tarafından kullanıldığı için adı değiştirilmeden korundu.
- `runtime_preflight.py --tenant-slug kasnak --require-ollama` tekrar çalıştı;
  Meta, veritabanı/RLS, Redis, worker/görevler, Ollama ve CPU dahil bütün
  kapıları geçti. CPU örnekleri `%70/%36/%19` (ortalama `%41,7`) ve çıkış kodu
  `0` oldu.

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
