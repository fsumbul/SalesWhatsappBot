# Doğal konuşma sürüm adayı — 15 Eylül 2026

Çalışma ağacındaki kod adayıdır; canlıya yayın, şirket sürümü yayınlama ve gerçek WhatsApp gönderimi yapılmadı. Başlangıç commit’i `4c0e2bb`; çalışma ağacında bu işten önce de değişiklikler bulunduğundan bu belge tüm farkların yalnız bu işe ait olduğunu iddia etmez.

## Karar ve araştırma

İki kanalda da konuşma metnini LLM üretir. Eski şirket sürümlerini okumak, hazır cevap yolunu yeniden açmaz. Sunucu veri erişimini, alan doğrulamasını, işlem yetkisini, onayları ve gönderimi yönetir. Kaynak metinler modelin kullanacağı kanıttır. [Mimari ve resmi kaynaklar](natural-conversation-2026-09-15.md).

Modelden yanıt alınamazsa hazır yetenek listesi üretilmez. Ashira ayrı teknik durum gösterir; WhatsApp işi cevap göndermeden hata kaydeder. Bu nedenle `reply` alanı boş olabilir. `answer_verified` yalnız semantik kontrolden geçildiğini gösterir; doğruluğun ispatı değildir.

## Tekrarlanabilir doğrulama

- API testleri: **579 geçti**, 2 bağımlılık deprecation uyarısı, 91,71 saniye. Düzeltme döngüsü değişikliğinden sonra ilgili **105 test**, son kapsam açıklaması değişikliğinden sonra **56 test tekrar geçti**.
- Testler gerçek geçici PostgreSQL üzerinde çalıştırıldı. Şirket/kullanıcı/oturum yalıtımı, mesaj tarih sınırları ve yönleri, tekrar istek, izinsiz değişiklik, STOP yarışı, tekrar gönderim ve model hatasında sıfır gönderim kontrolleri geçti.
- Değişen Python akışlarında Ruff ve `git diff --check` geçti. Mevcut yerel bağımlılıklarla TypeScript kontrolü ve Next.js üretim derlemesi geçti.
- Gerçek model: mevcut sunucudaki **qwen3.8-27b**, bildirilen bağlam 4.096 token, `LLM_ENABLE_THINKING=false`. Model sunucusunda ayar/deploy değişikliği yapılmadı.
- Canlı çıkarımlar yerel sentetik Deniz/Ece konuşmaları ve onaylı şirket bilgileriyle yapıldı. Üretim veritabanı kullanılmadı; müşteri mesajı/kişi sayıları değişmedi. Model denemeleri Meta teslimatının uçtan uca testi değildir.

İlgili betikler `apps/api/scripts/verify_natural_conversation.py`, `verify_natural_edges.py` ve `verify_natural_claims.py` dosyalarıdır. İlk iki betik yerel `leadpulse_test` veritabanı dışında çalışmayı reddeder. `verify_natural_edges.py --scope-only`, diğer son diyalog kayıtlarını koruyarak dört kapsam kombinasyonunu yeniler. Ham çıktıları yalnız JSON üretildi diye başarılı saymak yerine, aşağıdaki davranışlar ve kaynaklar elle incelendi.

## Gerçek modelde gözlenen davranış

**Ashira:** Altı turluk API konuşması bugün gelenleri getirdi, yazım hatalı kapsam sorusunu iki müşteri ve bugün olarak yanıtladı, Deniz’in tüm zamanlarına geçti, tek kişi kapsamını korudu, genel bilgi sorusunu cevapladı ve şirket genelinin bugünkü iki yönlü yazışmalarına döndü. Sayılar SQL sonuçlarıyla uyumluydu: bugün gelen 2, Deniz tüm zamanlar 3, şirket bugün iki yön 4 mesaj. Süreler **31,62–43,27 saniye**. [Ham kayıt: admin bölümü](../experiments/natural-conversation-qwen-2026-09-15.json).

**Aynı yazım hatalı sorunun son kapsam matrisi:** Gerçek SQL aracı ve ortak cevap üreticisi kullanıldı. Bu dört deneme, altı turluk API konuşmasına ek olarak araç sonucu açıklamasını yalıtarak sınar. Soru her seferinde aynıydı: “bu veriler kimlre ait? sadece bir kişniin mi yoksaa herkesin mi gün içindei”.

| Sorgu | Gerçek kayıtlar | Modelin açıkladığı kişi/zaman | Süre |
| --- | --- | --- | --- |
| Deniz, bugün | 1 kişi, 2 mesaj | Yalnız Deniz, 15 Eylül | 47,50 sn |
| Deniz, tüm zamanlar | 1 kişi, 3 mesaj | Yalnız Deniz, 12–15 Eylül | 57,34 sn |
| Şirket, bugün | 2 kişi, 4 mesaj | Ece ve Deniz, 15 Eylül, iki yön | 31,38 sn |
| Şirket, tüm zamanlar | 2 kişi, 6 mesaj | Ece ve Deniz, 12–15 Eylül, iki yön | 56,50 sn |

Tüm zamanlar sorgularında SQL tarih filtresi yoktur. Model bu örneklerde kayıtların fiilen bulunduğu tarih aralığını anlattı; “tarih filtresi yok” ifadesini aynen kullanmadı. Verileri yalnız bugün veya tek kişi diye yanlış tanıtmadı. [Son kayıt: scope bölümü](../experiments/natural-conversation-qwen-edges-2026-09-15.json).

**WhatsApp müşteri konuşması:** Üç turda bilinmeyen güncel fiyat, gökkuşağı sorusu ve işe dönüp yarın kesin teslimat sorusu denendi. Model fiyat/tarih uydurmadı; teslimatı doğrulayamadığını söyledi, konu değişiminde genel cevap verdi. Süreler **31,45–62,60 saniye**. Fiyat cevabında gereksiz bir teklif önerisi hâlâ var; daha doğal ve kısa dil için iyileştirme payı bulunuyor. Bunlar örneklem sonuçlarıdır; bütün şirket iddialarının her zaman doğru çıkacağı anlamına gelmez. [Son kayıt: customer_final bölümü](../experiments/natural-conversation-qwen-edges-2026-09-15.json).

**Teklif akışı:** Beş turda başlangıç, “Deniz Kaya” adı, “Deniz Kaan” düzeltmesi, ara genel soru ve işe dönüş denendi. Düzeltme gerçek taslak alanını değiştirdi. Ara soru, cevapları ve revizyonu değiştirmedi. İşe dönüş `intent=new` olarak doğrulandı ve kasnak türü adımına geçildi. Metinler model tarafından yazıldı; durum geçişleri sunucudan geldi. Süreler **29,20–54,46 saniye**. [Son kayıt: intake bölümü](../experiments/natural-conversation-qwen-edges-2026-09-15.json).

**İddia düzeltme:** Gerçek Qwen’e iki kasıtlı hatalı taslak verildi: kaynaksız fiyat nedenleri ve kaynaksız teslimat önkoşulları. Son denetleyici bunları reddetti; model yalnız doğrulayamadığı bilgiyi açıklayan yanıtlar üretti. Her örnekte kontrol–düzeltme–kontrol olmak üzere 3 çağrı; **26,85 ve 36,32 saniye**. [Ham kayıt](../experiments/natural-conversation-claims-2026-09-15.json).

## Saklanan başarısız denemeler ve kalan sınırlar

İlk kapsam denemesi tüm zamanları “bugün” diye anlattı ve eski kontrol bunu kaçırdı. Tarih modu, yerel saat gösterimi, araç talimatı ve denetim düzeltildi. Sonraki bir denemede yanlış tarih filtresi yakalandı fakat model aynı hatalı cevabı tekrar deneyerek süreyi tüketti; tekrarlanan aynı reddi durduran sınır eklendi.

Aynı yazım hatalı soru dört kapsamda tekrarlandığında, tek kişi/tüm zamanlar cevabı tarihi atladı. Kapsam açıklamasının hem kişi hem zaman boyutunu açıkça yanıtlaması istendi; bunun için soruya özel bir çalışma zamanı dalı eklenmedi. İlk çıktı `natural-conversation-qwen-edges-before-scope-2026-09-15.json` içinde saklandı.

Önceki müşteri denemesinde fiyat ve teslimatın bilinmediği söylendikten sonra kaynaksız ticari nedenler eklendi. Teknik gereksinim metninin kategorisi düzeltildi; denetleyicinin desteklenmeyen iddiaları karardan önce açıkça listelemesi zorunlu kılındı. Aynı önceki kayıtta bir ürün düzeltme turu zaman aşımına uğradı. Ana JSON’un `customer` bölümü bu eski denemedir ve **topluca kabul edilmiş sonuç değildir**. `failures=[]` yalnız betikteki mekanik kontrolleri anlatır. Diğer eski dosyalardaki `historical_not_accepted` işaretleri ve açıklamalar korunmuştur.

Son akış, her cümleyi şirket iddiası ve işlem sonucu açısından denetler; ancak üretici ve denetleyici aynı Qwen’dir. Makul görünen kaynaksız bir çıkarımı ikisi de kaçırabilir. Bu sınırlı örneklem güvenilirlik oranı veya sıfır halüsinasyon garantisi vermez. Kesin izin, kimlik, kayıt kapsamı, alan tipi ve gönderim denetimleri model kararına bırakılmadı.

Gecikme hâlâ yüksek: son müşteri örneklerinde tek yanıt 63 saniyeye yaklaştı. Genel konuşma görev döngüsüne girmese de yorumlama, yazma ve doğrulama çağrıları maliyetlidir. Daha hızlı sunucu, daha büyük bağlam veya ayrı bir doğrulayıcı bu adayda denenmedi. Düşünme ayarının bağımsız hız etkisi ölçülmedi.

Genel web araması müşteri botuna eklenmedi. Güncel şirket bilgisi kayıtlı araçlardan/yayımlanmış bilgi grafiğinden gelir; yoksa bilinmediği söylenir. Genel bilgi yanıtları mevcut model bilgisiyle üretilir; model internette araştırmış gibi konuşamaz.
