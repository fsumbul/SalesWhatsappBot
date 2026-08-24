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
