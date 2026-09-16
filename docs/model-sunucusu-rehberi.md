# Model Sunucusu Taşıma ve İşletim Rehberi

Bu rehber, SalesWhatsappBot'un model çalıştırma katmanını farklı bir sunucuya
veya farklı bir açık model ailesine taşımak içindir. Uygulama sunucusu ile model
sunucusu birbirinden bağımsızdır: model Linux, Windows, macOS veya Docker
üzerinde; uygulama ise başka bir makinede çalışabilir.

Bu projede dış bir yapay zekâ hizmeti kullanılmaz. Model sizin yönettiğiniz
Ollama, vLLM, LocalAI veya llama.cpp sunucusunda çalışır.

## 1. Çalışma modeli

```text
WhatsApp / Webhook
        |
        v
SalesWhatsappBot API + worker  ---- özel ağ / TLS ---->  Model sunucusu
        |                                                    |
        +---------------- PostgreSQL / Redis                 +-- Model dosyaları
```

Model sunucusunun görevi yalnız metin üretmektir. İş kuralları, yetkilendirme,
müşteri verisi, WhatsApp gönderimi ve güvenli fallback mantığı uygulama
sunucusunda kalır. Model erişilemezse veya beklenen yapılandırılmış cevabı
üretemezse uygulama rastgele model metni göndermek yerine güvenli fallback'e
geçer.

## 2. İki bağlantı türü

| Bağlantı türü | Ne zaman seçilir? | Gerekli uç nokta |
| --- | --- | --- |
| `ollama` | Ollama çalıştırıyorsanız | `http://sunucu:11434/api/chat` |
| `chat_compatible` | vLLM, LocalAI veya llama.cpp server kullanıyorsanız | `https://sunucu/v1/chat/completions` |

`chat_compatible`, bir servis markası değil; yaygın kullanılan HTTP chat
protokolünün adıdır. Sunucunuz JSON-schema ile kısıtlanmış cevapları
desteklemelidir; bu projedeki müşteri ajanı güvenli kararlarını bu sözleşmeyle
alır.

## 3. Hızlı kurulum: Docker ile Ollama

Bu seçenek aynı hostta veya Docker destekleyen herhangi bir sunucuda çalışır.
Özel işletim sistemi servisi ya da Arch Linux ayarı gerektirmez.

```bash
# Uygulama ve isteğe bağlı model servisini başlat.
docker compose --profile llm up -d --build

# Kullanacağınız modeli indir.
docker compose exec ollama ollama pull qwen3:8b
```

API'nin kullandığı ortam değişkenleri:

```dotenv
LLM_PROVIDER=ollama
LLM_MODEL=qwen3:8b
LLM_BASE_URL=http://ollama:11434
LLM_API_KEY=
```

Buradaki `ollama`, Docker servis adıdır. API Docker dışında çalışıyorsa
`LLM_BASE_URL=http://127.0.0.1:11434` kullanılır.

## 4. Ayrı bir Ollama sunucusuna bağlanma

1. Yeni model makinesine Ollama ve seçtiğiniz modeli kurun.
2. Model portunu yalnız özel ağ, VPN veya uygulama sunucusunun IP'si için açın.
3. Uygulamanın `.env` dosyasını güncelleyin.
4. API ve `agent_runtime` worker süreçlerini yeniden başlatın.

Örnek:

```dotenv
LLM_PROVIDER=ollama
LLM_MODEL=qwen3:8b
LLM_BASE_URL=http://10.20.30.40:11434
```

Önce uygulama sunucusundan bağlantıyı kontrol edin:

```bash
curl -fsS http://10.20.30.40:11434/api/tags
```

Çıktıdaki `models[].name` alanında `LLM_MODEL` değeri birebir bulunmalıdır.

## 5. vLLM, LocalAI veya llama.cpp ile bağlanma

Bu sunucularda standart chat uç noktası kullanılır. Sunucu HTTPS veya güvenilir
bir özel ağ arkasında olmalıdır.

```dotenv
LLM_PROVIDER=chat_compatible
LLM_MODEL=Qwen2.5-7B-Instruct
LLM_BASE_URL=https://model.internal.example/v1
# Koruma katmanı varsa, buraya kendi sunucunuzun bearer anahtarını yazın.
LLM_API_KEY=
```

Doğrulama:

```bash
curl -fsS https://model.internal.example/v1/models
```

Çıktıdaki `data[].id` alanında `LLM_MODEL` değeri bulunmalıdır. Sunucunun
`POST /v1/chat/completions` ve JSON-schema `response_format` desteğini, canlı
müşteri trafiğinden önce simülatörde doğrulayın.

## 6. Model değiştirme prosedürü

Model değişimi kod değişikliği değildir; ancak kalite değişikliği olabilir.
Bu nedenle aşağıdaki sıralamayı izleyin:

1. Yeni modeli eski sistemi kapatmadan yeni sunucuya indirin/yükleyin.
2. API dışındaki bir test ortamında `LLM_MODEL` ve `LLM_BASE_URL` ile deneyin.
3. Yapılandırılmış JSON yanıtlarının geçerli olduğunu doğrulayın.
4. Türkçe müşteri örneklerinde fiyat, stok, teslimat ve insan yönlendirme
   kurallarını test edin.
5. Uygulama sunucusundaki `.env` dosyasını güncelleyin.
6. API ile `agent_runtime` worker'ı birlikte yeniden başlatın.
7. Üretim öncesi kontrolü çalıştırın:

   ```bash
   python scripts/runtime_preflight.py --tenant-slug <tenant> --require-llm
   ```

8. İlk saatlerde model hata/fallback oranını ve worker loglarını izleyin.

Geri dönüş için önceki `LLM_PROVIDER`, `LLM_MODEL` ve `LLM_BASE_URL`
değerlerini saklayın. Sorun yaşanırsa bu üç değeri geri alıp API ve worker'ı
yeniden başlatmak yeterlidir.

## 7. Güvenlik ve ağ kuralları

- Model portunu internete açık ve kimlik doğrulamasız bırakmayın.
- Tercih sırası: aynı Docker ağı, özel VLAN/VPN, ardından TLS + erişim kuralı.
- `LLM_API_KEY` yalnız model sunucunuz kendi token korumanızı kullanıyorsa
  gerekir; anahtarı Git'e, dokümana veya loga yazmayın.
- Model sunucusunu uygulama veritabanına bağlamayın; yalnız API'nin model
  uç noktasına erişmesi gerekir.
- Model isteği ve cevabında müşteri verisi olabileceğinden, ters proxy/log
  katmanlarında gövde kaydını kapatın veya maskeleyin.

## 8. Sorun giderme

| Belirti | Kontrol |
| --- | --- |
| Bağlantı hatası | Uygulama hostundan `curl` ile model uç noktasını deneyin; DNS, firewall ve VPN'i kontrol edin. |
| Model bulunamadı | `LLM_MODEL` büyük/küçük harf dahil sunucunun listelediği adla aynı olmalıdır. |
| Güvenli fallback | Modelin JSON-schema çıktı desteğini ve uygulama/worker loglarını kontrol edin. |
| API çalışıyor, cevap yok | API ile `agent_runtime` worker'ın aynı `.env` dosyasını kullandığını doğrulayın. |
| Geçiş sonrası kalite düştü | Önceki model ayarına geri dönün; yeni modeli simülatör ve deney senaryolarıyla değerlendirin. |

## 9. Operatör kontrol listesi

- [ ] Model sunucusu özel ağda veya TLS ve erişim kontrolü arkasında.
- [ ] Seçili model sunucuda indirilmiş ve `LLM_MODEL` ile birebir eşleşiyor.
- [ ] API ve worker model sunucusuna erişebiliyor.
- [ ] JSON-schema yanıtı doğrulandı.
- [ ] `runtime_preflight.py --require-llm` geçti.
- [ ] Önceki üç LLM ayarı geri dönüş için saklandı.
- [ ] İlk üretim saatinde loglar ve fallback oranı izlenecek.

## 10. NVIDIA NIM harness ajanları (GPU sunucusu)

Guardrail, OCR/tablo, görsel doğrulama, embedding/rerank, arka plan LLM ve ses/çeviri
adaptörleri NVIDIA NIM konteynerlerine bağlanır. Kural değişmez: **müşteri mesajı, tenant
dokümanı/görseli/ses notu yalnızca sizin işlettiğiniz GPU sunucusundaki konteynere gider.**
build.nvidia.com "Free Endpoint" (`integrate.api.nvidia.com` vb.) girdileri kaydeder; uygulama
üretimde bu host'lara işaret eden her ayarı reddeder (`NIM_PUBLIC_HOST_DENYLIST`,
`runtime_preflight.py` → `production_configuration_errors`).

Kurulum (Linux + NVIDIA sürücü + NVIDIA Container Toolkit):

```bash
# GPU sunucusunda; NGC_API_KEY yalnız bu sunucunun .env dosyasında
NIM_BIND_ADDRESS=10.20.30.40 docker compose -f infra/docker-compose.nim.yml --profile nim up -d nim-guard-jailbreak
curl -s http://10.20.30.40:8011/v1/health/ready
curl -s http://10.20.30.40:8011/v1/openapi.json | head -c 400
```

- Portlar yalnız özel arayüze (`NIM_BIND_ADDRESS`) bağlanır; uygulama sunucusu VPN/özel VLAN
  veya mevcut Ollama tüneli deseniyle erişir. İnternete açık, kimlik doğrulamasız NIM portu bırakmayın.
- Her konteynerin gerçek istek/yanıt sözleşmesi `GET /v1/openapi.json` ile doğrulanır; adaptör
  fixture'ları (`apps/api/tests/fixtures/nim/`) bu çıktıya göre güncellenir.
- Uygulama tarafında yalnız etkinleştirdiğiniz özelliğin `*_BASE_URL` alanlarını doldurun; boş
  alan = özellik kapalı. Ortak `NIM_API_KEY` isteğe bağlıdır.
- Üretim kapısı: `python scripts/runtime_preflight.py --tenant-slug <tenant> --require-llm --require-nim`
  her yapılandırılmış NIM ucu `ready` değilse çıkış kodu 1 verir.
- VRAM planı ve servis/port listesi `infra/docker-compose.nim.yml` başındaki yorumdadır; iş paketi
  sırasına göre kademeli açın (plan: `docs/nvidia-nim-harness-agents-plan-2026-09-16.md` §7).
