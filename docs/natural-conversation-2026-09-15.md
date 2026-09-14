# Ashira ve WhatsApp: modelin yazdığı, kaynaklara dayanan konuşma

15 Eylül 2026 — sürüm adayı çalışması. Canlıya yayın yapılmadı.

## Araştırmadan çıkan karar

Konuşma metnini her zaman LLM üretir. Şirket metinleri cevap şablonu değil, doğrulanmış bilgi kaynağıdır. Düğme etiketleri, form alanları, işlem durumu ve hata göstergeleri uygulamanın arayüzüdür; asistanın konuşması yerine geçirilmez.

OpenAI’nin resmi function calling akışı, modelin araç istemesi, uygulamanın aracı çalıştırması, sonucun modele geri verilmesi ve modelin cevabı üretmesinden oluşur. Bu, bir niyet seçilip karşılığında hazır paragraf basılması değildir. [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)

Qwen de aynı protokolü destekler: araç açıklaması ve parametreler modele sunulur; model kullanıp kullanmamayı seçer; uygulama sonuçları geri verir. Belgelerdeki “template”, modelin araç protokolünün biçimidir; müşteriye gönderilecek hazır cümle anlamına gelmez. Mevcut uyumlu uçta şema kontrollü JSON çağrıları korunuyor; Qwen başka bir sağlayıcıyla değiştirilmiyor. [Qwen function calling](https://qwen.readthedocs.io/en/latest/framework/function_call.html)

Anthropic, ihtiyaç kadar karmaşık bir akış ve açık araç sözleşmeleri öneriyor. Bu projedeki karşılığı: genel konuşma ve önceki sonucun kapsamını açıklama doğrudan cevap yolunda; yeni veriye ihtiyaç duyan iş, araç döngüsünde. Her selam, yazım yanlışı veya sitem için yeni koşul eklenmiyor. [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)

## Uygulanan sözleşme

`conversation_language.py`, iki kanalın doğal cevap üretimi ve bütün cevabı kanıtla denetleme sınırıdır. Genel bilgiye izin verilir; “genel bilgi” etiketi şirket, fiyat, stok, teslimat veya işlem başarısı iddiasını denetim dışında bırakmaz. Denetleyici önce desteklenmeyen iddiaları tek tek listeler; liste boş değilse `supported=true` dese bile cevap kabul edilmez. Reddedilen cevap süre bütçesi içinde en fazla iki kez düzelttirilir. Kaynak kimliği, uzunluk ve yapı ayrıca kodla doğrulanır.

Ashira geçmişi `tenant + user + session` sınırında okunur. Önceki araç sonuçlarının kapsamı ve kanıtları ayrı saklanır. Asistanın önceki cümlesi şirket gerçeği veya yeni bir değişiklik yetkisi değildir. Okuma araçları çoklu görevde birleştirilebilir; değişiklikler mevcut inceleme/onay kartlarında durur.

Mesaj aracı gerçek `Message.created_at` üzerinden filtreler. Gün sınırları kullanıcının saat dilimindedir, varsayılan `Europe/Istanbul`. Araç sonucu seçili kişi/konuşma, sorgu, tarih modu, başlangıç ve bitiş, yön, toplam, farklı kişi/konuşma sayıları ve sayfa eksiklerini taşır. Şirket genelinde arama yapılması, birden fazla kişinin yazdığı anlamına gelmez.

WhatsApp’ta model ihtiyacı yorumlar; sunucu ilgili ürün/şirket bilgisini getirir; model cevabı yazar. Sosyal bilgiler hazır cevap olarak seçilmez. Teklif alanlarını serbest cümleden LLM önerir, mevcut alan ayrıştırıcıları ve durum makinesi doğrular. Düzeltme, yan soru ve onay ayrı yapılandırılmış önerilerdir. Eski konuşmadaki onay yeni değişikliğe veya gönderime yetki vermez. Arayüz düğmeleri de son konuşma cümlesini üretmek için modele döner.

Model erişilemezse veya cevap doğrulanamazsa `reply` boş kalır. Ashira teknik durumu ayrı UI alanında gösterir. WhatsApp işi gönderim yapmadan kapanır ve hata kaydedilir; yetenek listesi veya hazır insan devri mesajı gönderilmez. Açık insan desteği talebi mevcut devir yolunu kullanır. Teklifin kullanıcı tarafından onaylanmasıyla oluşan teknik inceleme, bilinmeyen sorudan otomatik devirden ayrı bir iş olayıdır.

`reply`, kartlar ve işlem sonuçları korunur. `answer_origin`, `answer_verified`, `result_scope` ve WhatsApp bilgi referansları ayrı alanlardır. `answer_verified`, semantik denetimden geçildiğini belirtir; kesin doğruluk garantisi değildir. Eski `response_source` alanı geriye uyumluluk içindir ve tek başına metin üreticisini göstermez.

Yeni şirket ayarlarında `response_mode=conversational` bulunur. Eski `strict/grounded` sürümleri okunabilir; kullanıcı talebi gereği bunlar hazır cümle yolunu tekrar etkinleştirmez. Yayınlanmış sürüm belgeleri üzerine yazılmaz.

## Doğrulama ve bilinen sınırlar

Gerçek Qwen değerlendirmesi, yerel geçici PostgreSQL ve sentetik konuşmalarla `apps/api/scripts/verify_natural_conversation.py` üzerinden yapılır. Üretim veritabanına veya Meta gönderimine bağlanmaz. Model erişimi mevcut Qwen uç noktasına tünel üzerinden yapılır. [Sürüm adayı değerlendirmesi](natural-conversation-release-review-2026-09-15.md), ham denemelerden hangilerinin güncel olduğunu ve insan incelemesinde kabul edilmeyen cevapları ayrı gösterir. Ana JSON içindeki `failures` yalnız betiğin mekanik kontrolleridir; boş olması konuşmaların tamamının kabul edildiği anlamına gelmez.

İlk gerçek denemede filtresiz mesaj sonucu “bugün” diye anlatıldı; semantik denetim bunu kaçırdı. Bu başarısız deneme `*-before-fix.json` olarak tutuldu. Kapsama açık tarih modu ve yerel gün eklendi, araç/denetim talimatları düzeltildi. Bu örnek, aynı modelden ikinci bir kontrol istemenin neden tek başına yeterli kabul edilmediğini gösterir.

Diğer bir denemede model fiyat ve teslimat bilgisi olmadığını söyledikten sonra kaynaksız nedenler ekledi; eski denetleyici bunları kabul etti. Teknik uygunluk gereksinimi yanlışlıkla ticari bilgi kategorisindeydi; kaynak metin değiştirilmeden `eligibility` olarak düzeltildi. Denetim, cevabın her yan cümlesini ve iddia edilen neden/önkoşul ilişkisini inceleyecek şekilde değiştirildi. Gerçek Qwen ile iki kasıtlı hatalı taslak tekrar denendi; model kaynaksız açıklamaları kaldırdı. Bu bir örneklem doğrulamasıdır, doğruluk garantisi değildir.

Qwen sunucusu 4.096 token bağlam bildiriyor. Paylaşılan üretim sınırı ve görev yürütücüsü, desteklenen `/tokenize` ucu üzerinden gerçek bütçeyi ölçer. Eski konuşma/kanıt parçalarının çıkarılması işaretlenir; mevcut soru ve denetlenen cevap sessizce kesilmez. Daha büyük bağlamı zorla açmak veya model sunucusunu değiştirmek bu sürümün parçası değildir.

`LLM_ENABLE_THINKING=false`, bu sunucunun desteklediği isteğe bağlı Qwen/vLLM uzantısıyla denenmiştir. Ayar boşken diğer OpenAI uyumlu sunuculara bu uzantı gönderilmez. Son ölçümler bu ayarla yapıldı; ayrı kontrollü karşılaştırma yapılmadığından bir hızlanma oranı iddia edilmiyor. [Qwen/vLLM ayarı](https://qwen.readthedocs.io/en/latest/deployment/vllm.html)

Güncel şirket bilgisi yalnız kayıtlı araçlar ve yayımlanmış bilgi grafiğinden gelir. Müşteri botuna genel web tarama yetkisi eklenmedi; internette arama yapmış gibi konuşmamalıdır. Genel bilgi cevaplarının bilimsel doğruluğu yalnız şirket kanıt denetimiyle ispatlanmış sayılmaz.
