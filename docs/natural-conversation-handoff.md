# Başka cihazdan devam — doğal konuşma

Dal: `codex/natural-conversation`.

```sh
git fetch origin
git switch --track origin/codex/natural-conversation
```

Bu dal, `main` üzerindeki diğer platform çalışmalarını içerir. `main` ile arasındaki fark Ashira/WhatsApp doğal konuşma adayı, tarih/kişi kapsamı, ilgili testler ve değerlendirme kayıtlarıdır. Telefon biçimi düzeltmesi, Meta şablon yönetimi, mevcut çoklu yönetici görevleri, arayüz animasyonları ve bağlantı toparlama işi `main` üzerindedir.

**Canlıya yayın yapılmadı.** 15 Eylül 2026'daki salt okunur kontrolde canlı müşteri botu hâlâ şirket sürümü 16 ve `strict` modundaydı. Qwen'in bilgi seçmesi `response_source=model` diye kaydedilebiliyor; bu eski kayıtta metni modelin yazdığı anlamına gelmiyor. Yeni katmanın `answer_origin` alanı bu ayrımı açıkça yapıyor.

Başlangıç için [sürüm adayı değerlendirmesini](natural-conversation-release-review-2026-09-15.md) ve [mimari/araştırma notunu](natural-conversation-2026-09-15.md) okuyun. Güncel gerçek Qwen kapsam/müşteri/teklif denemeleri `experiments/natural-conversation-qwen-edges-2026-09-15.json` içindedir. Ana değerlendirme JSON'unun eski müşteri bölümü başarısız denemeler de içerir; tamamı kabul edilmiş sayılmamalıdır.

Sonraki değerlendirmede özellikle mevcut Qwen gecikmesini ve denetleyicinin makul görünen kaynaksız şirket iddialarını kaçırabilmesini ele alın. Bu aşamada yeni bir yayına veya canlı müşteri mesajı göndermeye yetki verilmedi; kullanıcı çalışmayı burada durdurup ertesi gün incelemek istedi.

Yerel `.env`, SSH anahtarları, kurulu bağımlılıklar ve geçici test veritabanı Git'e dahil değildir. Diğer cihazda README'ye göre yerel ortam kurulmalıdır. Gerçek model deneme betikleri ayrıca erişilebilir model ucu ve yerel, geçici `leadpulse_test` veritabanı gerektirir. Üretim veritabanıyla veya gerçek Meta gönderimiyle çalıştırılmamalıdır.
