# Artı Kasnak WhatsApp botu: bilgi haritası ve video senaryosu

Bu belge, 24 Ağustos 2026 tarihinde erişilen Artı Kasnak resmî web sayfaları ile
2025 tarihli resmî ürün kataloğundan derlenen bilgileri ve WhatsApp tanıtım
çekiminde kullanılacak doğrulanabilir soru akışlarını bir araya getirir. Botun
çalışma kaynağı `apps/api/config/arti_kasnak.production.json` dosyasıdır.

## 1. Doğrulanmış şirket özeti

- Artı Kasnak, İstanbul merkezli bir asansör kasnağı üreticisidir.
- Resmî web sitesine göre Türkiye'nin 81 iline ve 50'den fazla ülkeye ürün
  sunar.
- Kendi dökümhanesi ile plastik/MC Nylon kasnak üretim altyapısı bulunduğunu
  belirtir.
- 2025 kataloğu tam otomatik robotik üretim altyapısı ve 50 kişilik nitelikli
  ekip bilgisi verir.
- Teknik çizime, malzeme seçimine, ölçülere, halat ve rulman bilgilerine göre
  özel üretim yapılabilir.
- Katalog minimum sipariş adedi zorunluluğu olmadığını belirtir; güncel MOQ,
  fiyat, termin ve ticari şartlar teklif sırasında teyit edilmelidir.
- Resmî site ISO-9001, TSE kalite onay raporları ile DIN ve EN standartlarına
  göre üretim iddiası taşır. Güncel sertifika, kapsam ve ürün uygunluğu teknik
  ekipten doğrulanmadan kesin belge beyanı yapılmamalıdır.
- Teknik destek ve satış sonrası destek sunulduğu belirtilir.

### Kaynak çelişkisi

Resmî web sitesi faaliyetin 2005 yılında başladığını, 2025 kataloğunun 3.
sayfası ise kuruluş yılını 2009 olarak belirtir. Bot bu farkı gizlemez ve kesin
kurumsal tarih için teyit ister. Tanıtım videosunda kuruluş yılı sorusunu ana
senaryoya koymamak en güvenli seçimdir.

## 2. Ürün ve teknik bilgi haritası

### Asansör kasnakları

- Captormal / MC Nylon 6 plastik asansör kasnağı
- GG-25 pik ve GGG-50 sfero döküm asansör kasnağı
- Palanga, saptırma, hidrolik ve motor kasnakları
- Çekiş kasnağı

Döküm grubu için resmî sitede özel savurma döküm, 2,5 m/sn hıza kadar balanslı
çalışma, daha düşük ses/titreşim, dayanıklılık ve düşük bakım ihtiyacı
özellikleri belirtilir. Captormal grubu için hafiflik, sessiz çalışma, enerji
verimliliği, korozyon ve aşınma direnci vurgulanır.

### Katalog ölçüleri

- Standart MC Nylon 6 / CK: 210, 240, 320, 360 ve 400 mm çap; 6,5, 8, 9 ve
  10 mm halat seçenekleri.
- Eco MC Nylon 6 / EC: 240, 320 ve 400 mm çap; EC240068, EC240078, EC240088,
  EC320068, EC320078, EC320088 ve EC400086.
- Standart döküm / DK: 210, 240, 320, 360 ve 400 mm çap; 6,5, 8, 9 ve 10 mm
  halat seçenekleri.
- Eco döküm / ED: 240 ve 320 mm çap; ED240064, ED240078, ED240088, ED320064,
  ED320078 ve ED320088.
- Çekiş kasnağı: TS160092 (160×92 mm, 4–6 halat) ve TS180118 (180×118 mm,
  5–8 halat); 6,5 mm halat ve 10 mm kanal mesafesi.
- Kayış kasnağı: BP110092, BP120118, BP160092 ve BP180118; 110, 120, 160 ve
  180 mm çap seçenekleri.

### Bileşenler

- Mil ve aks
- Yan destek ve yataklı rulman
- Rulman
- Mil tanımları: SL mil uzunluğu, GDi kanal mesafesi, SD mil çapı, GDe kanal
  derinliği, GW kanal genişliği.
- Yan destekler: 40, 45, 50, 55, 60, 65 ve 70 mm mil çapı; eco ve standart
  modeller.
- Yaygın rulman/mil eşleşmeleri: 6208/40 mm, 6209/45 mm, 6210 ve 6310/50 mm,
  6211 ve 6311/55 mm, 6212 ve 6312/60 mm.

### Kayış kasnakları

- Çelik kayış kasnağı: poliüretan kaplı çelik kayışlarla kullanım, sessiz ve
  verimli çalışma, yüksek taşıma kapasitesi ve düşük bakım hedefi.
- Plastik kayış kasnağı: hafiflik, düşük enerji tüketimi, sessiz çalışma,
  korozyon ve aşınma direnci; 2,5 m/sn hıza kadar ayrıca balans alma işlemi
  gerektirmeden kullanım iddiası.

### Teklif için gerekli veriler

1. Ürün/kasnak türü ve kullanım amacı
2. Teknik çizim; yoksa eldeki sistem ve ölçü bilgileri
3. Kasnak çapı A, genişliği B, halat adedi C, halat ölçüsü D, kanal mesafesi E
   ve rulman modeli
4. Mil/aks varsa SD, SL, GDi, GW ve GDe ölçüleri
5. Asansör kapasitesi ve hızı
6. İhtiyaç adedi ile teslimat şehri ve ülkesi

Bot ön hazırlığı yapar; nihai ürün seçimi ve uygunluk kararı teknik ekiptedir.

## 3. Bugün çalışan WhatsApp etkileşimleri

- `Merhaba` yanıtında üç hızlı başlangıç butonu: **Ürünleri göster**,
  **Şirketi tanı**, **Teklif al**.
- Ürün kataloğunda üç yönlendirme butonu: asansör kasnağı, kayış kasnağı ve
  bileşenler.
- Dörtten fazla seçenek olduğunda WhatsApp liste menüsü; üç veya daha az
  olduğunda doğrudan yanıt butonları.
- Tekil ürün bilgisinde **Ürünü incele** bağlantı butonu.
- Teknik katalog cevabında **Kataloğu incele** bağlantı butonu.
- Teklif niyetinde ilk yeterlilik sorusu ve **Talep formunu aç** butonu.
- Şirket bilgisinde **Web sitesini aç**, iletişim yanıtında iletişim sayfası
  bağlantısı.
- Buton kimlikleri kullanıcı metni gibi güvenilmez kabul edilir; yalnızca
  onaylı ürün veya bilgi kimliği çalıştırılır.
- Metin, buton ve link tek bir WhatsApp API isteğiyle gönderilir; ayrı mesaj
  gecikmesi oluşmaz.

## 4. Ana tanıtım videosu — 60–90 saniye

### Sahne 1 — Açılış

**Bilal:** `Merhaba`

**Beklenen bot davranışı:** Kısa karşılama ve **Ürünleri göster / Şirketi tanı /
Teklif al** butonları görünür.

**Anlatım:** “Müşteri ne yazacağını bilmek zorunda değil; bot ilk adımı
kendisi yönlendiriyor.”

### Sahne 2 — Ürün keşfi

**Eylem:** **Ürünleri göster** butonuna dokun.

**Beklenen bot davranışı:** Tüm ürün ailelerini özetler; **Asansör detayı**,
**Kayış detayı** ve **Bileşenler detayı** butonlarını gösterir.

**Eylem:** **Asansör detayı** butonuna dokun.

**Beklenen bot davranışı:** Captormal, döküm, motor ve çekiş kasnağını
WhatsApp liste menüsünde sunar.

### Sahne 3 — Üründen siteye geçiş

**Eylem:** Listeden **Döküm detayı**, ardından **Palanga detayı** seç.

**Beklenen bot davranışı:** Önce döküm ailesini, sonra palanga kasnağının
malzeme ve performans bilgisini verir. Son mesajda **Ürünü incele** bağlantısı
çıkar.

**Anlatım:** “Yanıt uydurulmuyor; şirketin onaylı ürün ağacından geliyor ve
müşteriyi doğru ürün sayfasına götürüyor.”

### Sahne 4 — Teknik soru

**Bilal:** `6211 rulman hangi mil çapına uygundur?`

**Beklenen bot davranışı:** 6211 ve 6311 için 55 mm mil eşleşmesini de içeren
onaylı tabloyu verir ve **Kataloğu incele** butonunu gösterir.

**Anlatım:** “Bot yalnızca genel satış sorularını değil, katalogdaki teknik
verileri de buluyor.”

### Sahne 5 — Teklife dönüşüm

Yeni konuşmada `Merhaba` yazıp **Teklif al** butonuna dokun veya doğrudan
`Teklif almak istiyorum` yaz.

**Beklenen bot davranışı:** İhtiyaç duyulan kasnak türünü ve kullanım amacını
sorar; aynı mesajda **Talep formunu aç** butonu görünür.

**Kapanış anlatımı:** “Müşteri ürün keşfinden teknik doğrulamaya ve teklif
talebine tek WhatsApp konuşmasında ilerliyor.”

## 5. Alternatif demo soruları

### Güçlü ve kısa sorular

- `Hangi ürünleri üretiyorsunuz?`
- `Palanga kasnağının malzemesi ve çalışma özellikleri nedir?`
- `Captormal kasnakların standart çap seçenekleri nelerdir?`
- `Döküm kasnakta 320 mm çap seçeneği var mı?`
- `6211 rulman hangi mil çapına uygundur?`
- `TS180118 kaç halat için?`
- `Yan desteklerde hangi mil çapları var?`
- `Özel ölçü üretim yapıyor musunuz?`
- `Teklif almak için hangi bilgileri göndermeliyim?`
- `İhracat ekibine nasıl ulaşabilirim?`

### Güvenli sınırı gösteren sorular

- `Bu ürünün bugünkü fiyatı nedir?`
- `Stokta kaç adet var?`
- `Teslim tarihi kesin ne zaman?`
- `Bu kasnak benim sistemime kesin uygun mu?`

Bu sorular için bot fiyat, stok, kesin termin veya mühendislik uygunluğu
uydurmamalı; iletişim/teknik ekip yönlendirmesi yapmalıdır. Sınır gösterimi
videonun en sonunda yapılmalıdır, çünkü yetkiliye devir konuşmanın otomatik
akışını durdurabilir.

## 6. Çekim kontrol listesi

1. WhatsApp konuşmasını temiz bir ekranda aç.
2. İlk mesajı `Merhaba` ile başlat; eski konuşma bağlamını kullanma.
3. Wi‑Fi ve Arch model sunucusunun açık olduğunu doğrula.
4. İlk turu önceden bir kez çalıştır; modelin belleğe alınması ilk yanıt
   gecikmesini azaltır.
5. Ekran kaydında bildirim önizlemelerini kapat.
6. Telefon numarası, erişim anahtarı ve yönetim ekranlarını kayda alma.
7. CTA bağlantılarının doğru Artı Kasnak sayfasını açtığını çekimden önce
   kontrol et.
8. Kuruluş yılı, güncel fiyat, stok, kesin termin ve kesin sertifika kapsamını
   ana tanıtım iddiası olarak kullanma.

## 7. Sonraki genişletmeler

- Meta Commerce kataloğu bağlanırsa ürün kartı ve çoklu ürün mesajları
  gönderilebilir.
- WhatsApp Flow oluşturulup yayımlanırsa çap, halat, rulman, kapasite, hız,
  adet ve teslimat bilgileri sohbet içinde form olarak toplanabilir.
- Teklif verileri CRM'e veya satış ekibi kuyruğuna otomatik aktarılabilir.
- Ürün görselleri onaylı medya kimlikleriyle ürün cevaplarına eklenebilir.
- Türkçe yanında İngilizce ürün/satış akışı aynı şirket bilgi uzayından
  üretilebilir.

Bu genişletmeler mevcut buton/liste/link akışından bağımsız değildir; ürün
kimlikleri ve onaylı bağlantılar aynı genel şirket JSON mimarisi üzerinden
yeniden kullanılabilir.
