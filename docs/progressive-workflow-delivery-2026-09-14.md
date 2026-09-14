# Progressive sohbet işlemleri — uygulama kaydı

Hedef aktiftir; kabul edilmiş planın tamamı henüz bitmedi. Bu dosya ara uygulama kaydıdır, canlı teslim onayı değildir.

## Çalışma kopyası ve koruma

- Aktif worktree: `/Users/binc/.codex/worktrees/fda1/SalesWhatsappBot`.
- Asıl kopya `/Users/binc/Documents/GitHub/SalesWhatsappBot` salt okunur incelendi. İki kopyanın Git tabanı `cd8e5cc62b03cace8bff03cc4caac5a0c55aef4f`.
- 98 ilgili değiştirilmiş/yeni dosya asıl kopyadan taşındı; her dosya SHA-256 karşılaştırmasıyla doğrulandı. Liste `/tmp/sales-workflow-import-manifest.json`. `.claude/`, `tmp/`, üretim env/anahtarlar ve cache taşınmadı. Asıl kopyaya yazılmadı.
- Web bağımlılığı asıl kopyanın `apps/web/node_modules` dizinine yerel symlink; pakete/commit'e alınmamalı.
- Eski release ZIP ve Windows staging paketi yeni kapsamın teslimatı değildir. Önceki görev, `kasnak-gpu-ve-e2e-yay-n` otomasyonunu PAUSED tuttu ve eski paketin yayınına açık kilit ekledi. Yeni doğrulanmış paket teslim edilmeden üretim dosyaları/DB/IIS/LIVE değişmeyecek.

## Uygulanan ortak altyapı

- `chat_workflows` ve `chat_workflow_actions`: kullanıcı + tenant + sohbet bağı, revizyon, adım, alanlar, durum, sonuç, idempotent eylem kaydı. Özel kullanıcı/tenant RLS; sohbet başına tek foreground işlem için partial unique index.
- Migration `f34cd56ef78a` (önceki head `f23bc45de67f`): eklemeli tablolar ve nullable `leads.person_name`. Mevcut şirket adları/geçmiş korunur. Yalnız yerel PostgreSQL test DB'sine uygulandı.
- `WorkflowView` v1 sunucu kontrollü alan/seçenek/sonuç sözleşmesi; ortak GET/start/action endpoint'leri `/admin-chat/sessions/{sid}/workflows` altında.
- Eylemler: update, continue, back, pause, resume, cancel, complete. Session lock sohbet turu ile paylaşılır. Her action expected_revision + client_operation_id taşır; receipt ve DB etkileri aynı transaction'da commit olur.
- Müşteri/irtibat, belirsiz kişi türü seçimi, ekip daveti, şirket oluşturma ve asistan oluşturma akışları eklendi. Form ve model aynı servisleri kullanır.
- Kişi adı şirket adından ayrıdır. En az bir iletişim kanalı gerekir. Workflow'lar arasında eşzamanlı mükerrer kişi oluşturma tenant advisory lock ile seri yapılır. Mevcut iletişim kaydında yeni kayıt/birleştirme yapılmaz, mevcut lead kimlikleri döner. Kayıt rıza/opt-in oluşturmaz.
- Davet sonucu link hazırdır; üyelik kabul edilmiş sayılmaz. Şirket oluşturma platform yetkisi ister ve cross-tenant canonical servis çağrısından sonra iki RLS context'i geri yükler.
- Yeni başlangıç diğer yarım işi bekletir; resume tek foreground işi geri getirir. Bilgi soruları workflow kaydını silmez. Revizyon çatışması güncel kartı döndürür.
- Model, yalnız sınırlandırılmış tür/eylem/alan önerir. Literal alan dayanağı ve rol sözcüğü doğrulanır; tamamlamak açık kullanıcı komutu ve ready/review durumu ister. Model başarısızlığında kayıtlı alanlar korunur.
- Ortak `workflow-card.tsx`: alanlar, adımlar, inline hata, inceleme, sonuç ayrıntıları, anlamlı ana eylemler. Sohbet gönderimi ve sohbet değiştirme öncesi yerel alanlar sunucuya yazılır; kayıt başarısızsa geçiş durur. Retry aynı client_operation_id'yi tutar.
- Geliştirmeye özel `/tr/dev/workflows` galerisi: boş/dolu/çalışıyor/hata/eylemleri kapalı/bekletilmiş/tamamlanmış. Production'da 404.

## Doğrulama

- Son tam suite: **435 passed / 0 skipped**; gerçek PostgreSQL/RLS + Redis. 6 yeni workflow testi mevcut 429 teste eklendi. Çıktı `/tmp/sales-workflow-tests.txt`.
- Ruff temiz, strict mypy 110 kaynak dosyasında temiz.
- Son Next production build başarılı; galeri ve şirket/asistan eklemeleri dahil. Çıktı `/tmp/sales-workflow-web-build.txt`.
- Gerçek Chromium + gerçek API/DB: 10 yeni kontrol geçti (kişi türü, kart alanları, geri/düzeltme, sohbet geçişi öncesi kayıt, beklet/devam, kişi sonucu, yenileme sonrası kayıt, mobil yatay/dikey sayfa taşması yok, tek composer, JS hata yok). Script `apps/web/e2e/progressive_workflows.py`. Bu test guided eylem kullanır; model veya Meta başarısı değildir.
- Görseller `/tmp/sales-workflow-desktop.png`, `/tmp/sales-workflow-mobile.png`. Son kontrol, ana eylem görünür olana dek bekleyerek stabil görünümü yakalar.
- `verify_company_models.py` yeni doğal workflow senaryolarıyla genişletildi. Henüz gerçek modelde çalışmadı.
- Mevcut Vast 50360379 salt okunur API kontrolünde `actual_status=exited`, `cur_state=stopped`, `intended_status=stopped`. Yeni GPU/sağlayıcı/ücretli kaynak açılmadı.

## Devam edilecek işler

1. Şirket/asistan yönetimi, bilgi aktarımı, test/yayın, teklif kayıtları, gelen kutusu ve WhatsApp hazırlama/gönderim/durumun kalanını bu sözleşmeye taşı. Eski yönetim panellerini yeni işlemler için kartın içine bütünüyle gömme yaklaşımını kaldır; eski geçmiş kartları okunabilir tut.
2. Ortak alan grubu/seçenek/kayıt seçici/dosya yükleme/liste/değişiklik özeti/ilerleme bileşenlerini tüm akışlarda kullan. Galeri durumlarını erişilebilirlik ve hatalar açısından genişlet.
3. Mükerrer kişi sonucunda kimlik listesinin yanında kişi ayrıntısı/seçimi sun. Kayıt listeleri ve arama ortak bileşen kullanmalı. Tamamen mesajla akışlarda iki adaylı geri dönüş seçimini geliştir.
4. Uzun işlerde progressive adımları, eski adımı değiştirince önizlemenin yenilenmesini, model kesintisini, belirsiz gönderimi ve güncel rol kontrolünü tüm adapter'larda doğrula. WhatsApp'ın mevcut sending-before-network/outbox garantisini koru; DB-only TurnTransaction adapter'ını harici gönderim servisine verme.
5. Kartta yerel alanların yazılırken otomatik saklanması/bağlantı hatası davranışını tamamla; şu an açık “Bilgileri sakla”, devam/beklet ve iş/sohbet değiştirme öncesi saklama vardır. Sayfa yenilemede sunucuda kaydedilmiş adım korunur; henüz kaydedilmemiş tuş vuruşları için otomatik saklama tamamlanmadı.
6. Bekletilmiş kartlar kompakt açılır ayrıntıya alındı. Odak, klavye ve ekran okuyucu kontrollerini genişlet. Workflow'ları sohbet içindeki ilgili turla ilişkilendirerek uzun geçmişte kart konumunu koru; şu anda güncel workflow listesi mesajların altında gösterilir.
7. Tam backend/build/E2E ve gerçek Qwen kabulü; yalnız mevcut GPU hazır olduğunda. Sonra yeni manifest/paket; eski staged sürümü kullanma. Gerçek Meta teslimatı için yeni kullanıcı test mesajı gerekir, başkasına mesaj/e-posta yetkisi yok.

## Yerel çalıştırma ayrıntıları

Python `/tmp/saleswhatsapp-venv/bin`; DB `127.0.0.1:55432/leadpulse_test`, restricted `leadpulse_app/leadpulse_app_dev`; Redis 56379. `REQUIRE_DB_TESTS=1`. Kaynak `.env` kopyalanmadığı için local test env değişkenleri açık geçirilir.

Bu görev test servislerini ayrı API 58010 ve web 53010 portlarında açtı; son browser kontrolünden sonra yalnız bu iki test sürecini kapattı. Next'i **`--hostname localhost`** ile başlat: `127.0.0.1` hostname + localhost URL, mevcut Next/next-intl ile rewrite proxy döngüsü yaptı. `API_BASE_URL=http://127.0.0.1:58010`, `WEB_BASE_URL=http://localhost:53010`. Başka servisleri durdurma.

Yalnız yerel browser test hesabı `/tmp/sales-workflow-e2e-account.json` dosyasında. Üretime taşıma. Yeni browser testi `E2E_ACCOUNT_FILE` ve `E2E_WEB_URL` destekler.

## İkinci uygulama turu: bilgi / aktarım / test / yayın

Önceki hedef turu somut ilerlemeydi; bu turda devam edildi. Goal hâlâ aktif; tüm kabul planı tamamlanmadı.

- Yeni migration head **f45de67fa89b**, `chat_workflows.state` ekler. Her workflow'un özel proposal/revizyon/önceki LIVE/record choice/test output snapshot'ı bu RLS korumalı alandadır. Yalnız yerel test DB'sine uygulandı; üretim/staging yok.
- `workflow_agents.py`: configure/publish/test adapter'ları kanonik AgentService, builder, import, proposal, test servislerini kullanır. Ayrı chat preview slotunu paylaşmazlar.
- Metin, JSON ve CSV aktarımı aynı yapılandırma workflow'una bağlandı; JSON/CSV dosya seçimi ve içerik alanı; isteğe bağlı CSV sütun eşlemesi JSON alanı. Import hataları kart içinde görünür, kısmi taslak uygulanmaz.
- Önce/sonra karşılaştırması; geri/düzeltme bekleyen proposal'ı reject edip yeni önizleme üretir. Taslağa kaydetme ile yayınlama ayrı somut sonuçlardır. Publish, draft revizyonu ve önceki LIVE kimliğini tekrar doğrular.
- Model/builder hatası savepoint içinde geri alınır; dış workflow alanları ve failed sonucu/eylem receipt'i commit olur. Model hatasında kullanıcı metni kaybolmaz.
- Test, seçilen gerçek sürüm + revizyonla çalışır. Başka asistana ait version ID açıkça reddedilir; sessizce başka sürüme geçilmez. Aynı sohbet + sürüm/revizyonda test geçmişi kanonik test session'ında korunur. Fallback sonucu completed sayılmaz; kart failed olur, içerik ve çıktı saklanır.
- Model/test çıktısı ve karşılaştırma ortak `workflow-details.tsx` bileşeninde. Tamamlanan kartın önizleme/çıktı ayrıntıları kapalı başlar. Müşteri testinin kaynak/model/sürüm/süre/fallback bilgisi ayrı gösterilir. Kartta ikinci bir sohbet kutusu yoktur.
- Yeni guided menü komutları: `action:Bilgi ekle`, `action:Müşteri testi`, `action:Taslağı yayınla` artık WorkflowView kullanır. Planner şeması ve gerçek model doğrulama beklentileri configure/test/publish workflow niyetleri için güncellendi.
- **Önemli kalan geçiş:** `workspace_tools` eski yazma/panel yolları hâlâ kodda ve legacy Intent(tool=workspace) ile çağrılabilir. Bunları yeni işlemlerden tamamen ayır; eski geçmiş okunabilirliği/önizleme onayını koruyarak router/menu/account/record navigation'ı ortak akışa taşı. Bir eski compatibility testi yeni guided yayını kullanmak yerine açık legacy model intent'iyle çalışacak biçimde güncellendi; yeni yayın kabulü progressive testte revizyon çatışmasını doğrular. Bu durum tüm ürünün taşındığı anlamına gelmez.

### İkinci tur kanıtları

- Tam gerçek PostgreSQL/RLS + Redis suite **438 passed / 0 skipped** (9 workflow testi, eski 429 test). Son version ID/mesaj boyu kontrolleri için odaklı workflow testleri tekrar çalıştırıldı.
- Ruff temiz; strict mypy **111** kaynak dosyası; Next production build ve TypeScript başarılı.
- Yeni gerçek Chromium script `apps/web/e2e/progressive_agents.py`: **11 kontrol** (asistan oluşturma, JSON dosyası, önce/sonra, önizlemeyi düzenleme, taslak/yayın ayrımı, açık yayın, test sürümü, model kesintisinde mesajı koruma, WhatsApp çağrısı yok, mobil taşma yok, JS hatası yok). Yerel synthetic test şirketinde yayın yapar; üretime temas etmez. Model başarı testi değildir, provider yapılandırılmamış hata yoludur.
- Önceki kişi E2E'sinin 10 kontrolüne eklenir. Görseller `/tmp/sales-workflow-publish.png`, `/tmp/sales-workflow-test-outage.png`.
- GPU bu turda başlatılmadı veya yeni kaynak kiralanmadı; gerçek Qwen kabulü hâlâ eksik. Son doğrulanmış Vast durumu önceki turdaki exited/stopped. Eski paket dağıtım kilidi/PAUSED otomasyonu devam ediyor.

### Sıradaki uygulama öncelikleri

1. Eski `workspace` panel girişlerini ortak kayıt listesi/seçici ve iş akışlarına taşı. Kişilerde mükerrer sonucun somut kayıt ayrıntısı/seçimi, asistan/şirket/ekip/sürüm/teklif/konuşma listeleri; şirket bilgilerini okuma, rol değişikliği/aktivasyon/sahip daveti, sürüm geri alma.
2. Gelen kutusunda konuşma seçimi, manuel yanıt, botu devam ettirme; WhatsApp alıcılar/şablon/izin/önizleme/kuyruk/delivery ortak workflow. Harici POST öncesi commit invariant'ını asla DB-only TurnTransaction ile sarma.
3. Otomatik alan saklama, workflow'un ilgili mesaj turunda konumunu koruma, accessible ortak record/file/list/choice bileşenleri ve kapsamlı galeri. CSV sütun eşlemesini ham JSON alanı yerine etiketli ortak alanlarla ilerlet.
4. Tüm doğal dil akışları için gerçek Qwen kabulü ve tüm UI/tenant/concurrency testleri. Ancak bundan sonra yeni paket ve canlı teslim adımları; eski staged ZIP kullanılamaz.

## Üçüncü uygulama turu: ortak kayıtlar ve ekip erişimi

Önceki tur somut ilerlemeydi; bu tur da yeni uygulama ve doğrulama sağladı. Goal hâlâ aktif.

- `workflow_records.py` + ortak `workflow-records.tsx`: asistanlar, kişiler, şirketler, ekip, şirket bilgileri ve sürümler; sunucuda arama, 20 kayıtlık sayfalar, açılır kayıt ayrıntıları, kayıt üzerinden izinli sonraki işlemler.
- Asistan/ekip/şirket/bilgi/sürüm için **guided hesap/menü girişleri** artık panel yerine `records` WorkflowView açar. Hesap menüsüne Kişiler eklendi. Asistan kaydından bilgi değişikliği, okuma, sürüm, test ve yayın; sürüm kaydından o sürümü test etme. Kaynak liste bekletilir ve araması saklanır.
- `launch` eylemi parent revizyonu + client işlem kimliğiyle seri çalışır; child UUID namespace ile idempotent start ve parent receipt aynı transaction'dadır. Başka tenant'ın kayıtları yeniden sorguda reddedilir. Liste erişim yetkisi retry öncesinde de kontrol edilir.
- Kişi listesi ad/şirket/iletişim ayrımını korur ve telefon/e-posta ile aranabilir. Bilgi listesi şirket adı, ürün/hizmetler ve onaylı bilgi/kaynaklarını gösterir.
- `workflow_members.py`: ekip listesinde normal kullanıcı kaydından yetki/etkinlik değişikliği. Önce/sonra yalnız değişen alanları gösterir; tamamlamada tenant kilidi altında actor güncel rolü ve hedefin önceki erişim durumu tekrar kontrol edilir. Kanonik `patch_user` son aktif sahibin korunmasını sağlar; platform rolü düzenlenmez.
- 409 hata geri bildirimi düzeltildi: yalnız sunucunun güncel WorkflowView döndürdüğü revizyon çatışmasında kart otomatik yenilenir. Diğer 409 nedenleri (son sahibi koruma, eski agent version vb.) kendi mesajıyla gösterilir. `ApiError.detail` eklendi. Son aktif sahip hatası Türkçeleştirildi.
- Kart kaydı sürerken sohbet geçişi artık kaydın sonucunu bekleyebilir (`saveWaiters`). Tarayıcı testinde ayrıca bekletilmiş eski kartın test seçicisiyle seçilmesi ve zorunlu alan yıldızının exact label eşleşmesini bozması giderildi; gerçek ürün arızası diye sayılmadı. Son test aktif kartı ve doğru erişilebilir etiketi hedefler.
- Bu tur yeni DB migration yok; head `f45de67fa89b`.

### Üçüncü tur doğrulama

- Tam suite **441 passed / 0 skipped**; yeni 3 test ile toplam 12 workflow kabul testi. Kayıt arama/child idempotency/cross-tenant seçim, kişi iletişim ayrımı, son sahip ve eski rol önizlemesi.
- Ruff temiz, strict mypy **113** kaynak dosyasında temiz; Next production build/TypeScript başarılı.
- Gerçek Chromium `apps/web/e2e/progressive_records.py`: **11 kontrol** geçti. Hesap menüsü → ortak liste → kayıt oluşturma → arama → seçili asistanı yapılandırma → arama bağlamının korunması → büyük AgentPanel yok → ekip değişiklik önizlemesi → son sahip hatasının özgül gösterimi → tek composer → mobil taşma/JS hatası yok.
- Görseller `/tmp/sales-workflow-records-desktop.png`, `/tmp/sales-workflow-records-mobile.png`. Son küçük düzenleme: member önizlemesinden değişmeyen alanlar çıkarıldı.
- `verify_company_models.py` kayıt niyetlerinde türün yanında category bilgisini de doğrular. Gerçek Qwen hâlâ doğrulanmadı. Bu tur Vast'a yeni start/rental isteği yok; üretim veya staging değiştirilmedi.

### Öncelikli kalan kapsam

1. Gelen kutusu ve WhatsApp/outbox progressive workflow; gerçek harici gönderim için mevcut at-most-once/commit-before-network sınırını koru. Teklif/teknik talep listeleri, konuşma ayrıntıları ve işlemlerini ortak record listesine bağla.
2. Router'daki `workspace` doğal/model fallback yolları hâlâ eski `workspace_tools` panellerini oluşturabilir. Yeni işlemlerden bu yolu bütünüyle kaldır; yalnız eski kayıtlı kartların okunması ve sunucudaki eski önizlemelerin tamamlanması için gereken uyumluluğu tut. Henüz ürünün tüm işlemleri taşındı deneme.
3. Şirket sahibini yeniden davet etme ve sürüm geri alma gibi kalan yönetim işlemleri. Kişi mükerrer sonucunda doğrudan ortak kayıt ayrıntısına geçiş. Genel kayıt sayfalama ve boş/izinli/izinsiz durum galerilerini ve klavye/ekran okuyucu kontrollerini genişlet.
4. Alanların yazılırken otomatik sunucuya saklanması, workflow kartının ilgili sohbet turunda konumu, CSV sütun eşlemesini görsel alanlara dönüştürme. Bu aşamada explicit save/continue/pause ve sohbet geçişi öncesi saklama vardır.
5. Bütün eski browser testlerini yeni ortak arayüzle eşleştir; eski 16 platform UI script'i hâlâ büyük panellere yönelik adımlar içeriyor. Yeni workflow script'leri bunu kısmen kapsar, tüm eski kabul kapılarının yerine geçtiği henüz kanıtlanmadı.
6. Gerçek Qwen kabulü (yalnız mevcut GPU), yeni doğrulanmış paket/manifest ve ardından önceki dağıtım planındaki canlı kabul. Eski staged ZIP yasak; otomasyon PAUSED kilidi korunuyor.

## Dördüncü uygulama turu — ortak gönderim hazırlığı

- Yeni `workflow_outreach.py` mevcut outbox'a alıcılar → Meta onaylı şablon/alanlar → son inceleme → kuyruk sonucunu bağlar. Ortak kart hiçbir mesaj POST'u yapmaz; `queue_batch` doğrulamaları ve mevcut worker'ın commit-before-POST/at-most-once sınırı korunur.
- Son onay workflow receipt ve parti kuyruğunu aynı transaction'da saklar. Eski incelemede geri/düzenleme taslağı iptal eder. Başlatılmış gönderimin içeriği değiştirilemez. Başka işe geçiş arka planda çalışma anlamındadır; iptal yalnız draft/queued alıcıları durdurur.
- Alıcı bazında kuyruk/Meta kabulü/gönderilme/teslim/okunma ayrımı ortak kayıt satırlarında görünür. GET workflow listesi artık session kilidi altında outbox durumunu eşler ve yalnız değişen görünümün revision'ını artırır. Tarayıcı bekleyen teslimatları 5 saniyede bir yeniler; sekme gizliyken durur. Aynı revision'daki diğer kartların yazılan alanları sıfırlanmaz.
- Ana başlangıç önerilerine “WhatsApp gönderimi hazırla” eklendi. Şablon alanları sunucunun gerçek seçeneklerinden türetilir. Modelden gelen `consent_evidence` reddedilir; formdan girilir. Mevcut kayıtlı izinler yine kanonik kuyruk servisi tarafından kullanılabilir.
- Bağlantı/şablon sağlayıcısı hatasında kaydedilmiş alanlar korunur. HTTPX bağlantı hatası da kapsanır.

### Dördüncü tur doğrulama ve sınırlar

- Tam backend suite **444 passed / 0 skipped**. Son HTTPX hata varyantının eklenmesinden sonra hedefli gönderim suite **4 passed** (toplam test sayısı 445; 445'in tamamı bu son küçük düzenlemeden sonra tekrar çalıştırılmadı). Ruff ve strict mypy **114** kaynak dosyasında temiz.
- Next production build/TypeScript başarılı. Son arka plan etiketleri ve biçimlendirme ardından TypeScript ayrıca kontrol edildi.
- Gerçek Chromium `progressive_outreach.py`: **7 kontrol** — başlangıç önerisi, bağlantı yokken alan koruma, geçmişten yenileme, beklet/devam et, tek composer, mobil taşma yok, JS hatası yok. Ekran görüntüsü `/tmp/sales-workflow-outreach-mobile.png` görsel olarak kontrol edildi. Bir tekrar denemesinde durum bekleme zaman aşımı oldu; yeniden çalıştırma geçti. Bu script gerçek Meta şablon seçimini/gönderimi test etmez; başarılı şablon/kuyruk akışı PostgreSQL testlerinde sağlayıcı okumaları mock edilerek doğrulandı.
- Eski doğal `tool=outreach` yolu hâlâ eski kartı üretebilir. Yeni ortak workflow yolu çalışır; doğal niyetlerin tamamının ortak akışa taşındığı henüz iddia edilmez. Kuyruk çalışan/teslimat UI'sının gerçek tarayıcı senaryosu ve canlı kabul hâlâ gerekiyor.
- Üretim, eski staging ve GPU kaynaklarında değişiklik yok. Önceki kalan kapsam listesi (inbox/teklifler, eski panel yollarını kaldırma, autosave, sohbet turuna konumlandırma, CSV eşleme, gerçek Qwen/WhatsApp ve yeni dağıtım paketi) geçerlidir. Hedef tamamlanmadı.

## Beşinci uygulama turu — alanları otomatik saklama

- Ortak kart düzenlenebilir alanları son değişiklikten 800 ms sonra `update` ile otomatik saklar. Otomatik kayıt sırasında alanlar açık kalır; sunucunun onayladığı snapshot ile istek sürerken yazılan yeni alanlar birleştirilir. Kayıt hatasında otomatik tekrar döngüsü yapılmaz; mevcut idempotent yeniden deneme kullanılır.
- Sohbet geçişi, yeni sohbet veya çıkış öncesi flush, sürmekte olan isteği ve onun ardından yazılan son alanları bitirir. Tamamlanmamış kayıt varken sayfadan ayrılmada standart tarayıcı uyarısı vardır. Kaydedilmemiş karakterler için anlık/crash-proof saklama iddiası yoktur; 800 ms debounce ve ağ onayı gerekir.
- “Kaydedildi” yalnız alanlar son sunucu görünümüyle eşleşirken gösterilir. Kayıt sırasında kullanıcının yazmasını engelleyen alan kilidi kaldırıldı; işlem düğmeleri sıra korumasını sürdürür.
- `progressive_autosave.py` gerçek Chromium + API + PostgreSQL üzerinde **7 kontrol** geçti: otomatik saklama, geciken istek sırasında yazma, sohbet değişmeden son karakterleri boşaltma, geçmişten yeniden açma, sunucuda commit sonrası yanıt kaybında aynı işlem kimliğiyle retry, çıkış öncesi saklama ve JS hatası olmaması.
- Mevcut `progressive_workflows.py` **10 kontrol** ile regresyon geçti. Next production build ve TypeScript başarılı. Backend değişmedi; önceki backend sonuçları bu tur yeniden çalıştırılmadı.
- Vast instance **50360379** doğrudan read-only API ile yeniden kontrol edildi: `actual_status=exited`, `cur_state=stopped`, `intended_status=stopped`. `status_msg` içindeki eski running metni durum kanıtı sayılmadı. Start/kiralama yapılmadı; gerçek Qwen kabulü hâlâ yok.
- Sonraki kapsam değişmedi: workflow kartlarını ilgili sohbet turuna yerleştirme, görsel CSV sütun eşleme, inbox/teklifler ve kalan yönetim işlemleri, eski doğal niyet/panel yollarının bütünüyle ortak akışa taşınması, kapsamlı browser/galleri ve gerçek model/WhatsApp kabulü. Hedef aktif, tamamlanmadı.

## Altıncı uygulama turu — sohbet konumu ve görsel CSV eşleme

- `chat_workflows.anchor_sequence` sunucuya ait kalıcı konum bilgisidir. Sohbetten başlayan işlem o turun sırasına, formdan başlayan işlem mevcut sıraya, kayıt listesinden açılan alt işlem ebeveynin sırasına bağlanır. İstemci konumu değiştiremez. Bekletme/düzenleme/devam etme konumu taşımaz.
- `f56ef78ab90c_workflow_anchor.py` migration yerel PostgreSQL'e uygulandı; 310 mevcut satır exact start receipt/turn eşleşmesi, yoksa zaman bakımından önceki tur ile dolduruldu. Üretime uygulanmadı. Eski kayıtların alt işlem bağlantısı olmadığı durumlarda önceki tur yaklaşımı kullanılır.
- Mesaj geçmişi `sequence` döndürür. Frontend kartları ilgili asistan yanıtının ardından, ayrı kardeş elemanlar olarak gösterir; mesaj geldikçe sohbetin altına kopyalamaz. Yeni alt işlem açılınca ilgili karta kayar. Başlangıçtan önceki doğrudan form kartları 0 sırasında, geçmişte durur.
- CSV yapılandırmasında ham JSON eşleme alanı gizlendi. Dosyanın ilk satırından gerçek sütun seçenekleri oluşturulur; altı alan için anlaşılır etiketli select kontrolleri kullanılır. Standart başlıklar otomatik seçilir; özel başlıkların eşlemesi workflow alanlarında saklanır. Eski JSON mapping verisi okunmaya devam eder. İçe aktarma kanonik servise gider; yinelenen başlıklar sessizce son sütunu seçmek yerine hata verir.

### Altıncı tur doğrulama

- Konum migration'ı ardından tam backend **446 passed / 0 skipped**. CSV eklemesinden sonra hedefli progressive suite **15 passed**, kanonik company workspace regresyonu **6 passed**. CSV iki yeni test ekledi (toplam 448 test; 448'in tamamı son CSV değişikliğinden sonra tekrar çalıştırılmadı).
- Ruff ve strict mypy **114** kaynak dosyasında temiz. Next production build ve TypeScript başarılı.
- Gerçek Chromium autosave + konum **8 kontrol**: yeni mesajdan sonra ve geçmiş yeniden açılınca kart tek kopya ve ikinci mesajdan önce kalır; otomatik kayıt/yanıt kaybı/çıkış davranışları da doğrulanır.
- Gerçek Chromium agent/import/publish/test **14 kontrol**: JSON ve CSV dosyaları, gerçek başlıklardan seçim, CSV taslağı, değişiklik önizleme, yayınlama ve model yokken hata yolu. Mobil CSV eşlemesi `/tmp/sales-workflow-csv-mapping-mobile.png` görsel olarak kontrol edildi; taşma yok.
- Üretim ve GPU değiştirilmedi. Qwen/gerçek WhatsApp kabulü yok. Öncelikli sonraki iş: eski doğal niyetlerin büyük panel yollarını kaldırıp ortak workflow/record yollarına taşıma; inbox/teklif/kalan yönetim işlemlerini tamamlama. Eski platform E2E script'leri, geniş durum galerisi ve canlı kabul/yeni paket hâlâ kapsamda. Hedef tamamlanmadı.

## Yedinci uygulama turu — yönetim niyetleri ve gerçek Qwen kabulü

- `workflow_intents.normalize` doğrulanmış eski model sözlüğünü router sınırında ortak akışa çevirir: agents/knowledge/versions/team/platform kayıt listeleri; invite/create_company/create_agent; configure/test/publish. Alan dayanağı ve yetki sözcüğü kontrollerinden sonra çalışır; eksik rol veya onay üretmez. Model/system talimatı da ortak workflow yolunu tercih eder.
- Eski kaydedilmiş workspace onay/iptal/seçim kartlarının uyumluluğu korunur. Eski asistan oluşturma onayı artık yeni büyük knowledge paneli üretmez. Inbox ve doğal outbox yolları henüz bu dönüşüme dahil değildir; tüm eski yollar kaldırıldı deneme.
- `test_chat_workspace.py` içindeki `seed_legacy_turn`, tarihsel kart/receipt fixture'ı üretirken yeni çeviriyi yalnız test içinde atlar. Normal `turn()` atlamaz. Gerçek yeni router yolu `test_workflow_intents.py` içinde 11 işlem + oluşturma/onay/idempotency + rol/dayanak kontrolleriyle ayrıca doğrulanır. Bu tarihsel fixture testlerini yeni UI kabulü diye sunma.
- İlk tam suite 461; gerçek modelin ortaya çıkardığı `format=text` düzeltmesi için ek test sonrası tam suite **462 passed / 0 skipped**. `format` yalnız text/json/csv kodlarını kabul eden semantik alandır; müşteri içerikleri hâlâ literal dayanak ister. Ruff ve mypy **115** kaynak dosyasında temiz.
- Gerçek Chromium eski küçük harf/aksan alias → yeni ortak UI **9 kontrol** geçti. Bu alias testi model kullanmaz.

### GPU engeli çözüldü — önemli güncel durum

- Teslim planının `docs/company-platform-delivery-2026-09-14.md:102` talimatı aynı instance için start isteğini yenilemeyi zaten yetkilendiriyor. Salt okunur stopped kontrolleriyle yetinmek gerçek ilerlemeyi engelliyordu. **50360379** için PUT state=running bu tur success döndü; sonraki GET actual/cur/intended **running** doğruladı. Yeni instance/sağlayıcı/kiralama yapılmadı.
- Mevcut SSH: `root@5.2.174.20:23619`, local key `/Users/binc/.ssh/arti_vast_test_ed25519`. Strict known-host doğrulaması geçti. Sağlayıcı proxy adresi ssh7.vast.ai:10378; doğrudan port aynı kaldı.
- `/etc/vast-agents-guide.md` tamamı okundu. Mevcut `/workspace/arti-test/serve.sh` workload'ı otomatik başladı; model yeniden kurulmadı/ayarlanmadı. vLLM alias **qwen3.8-27b**, loopback **18080**, model len **4096**, tek sequence. `/v1/models` ve gerçek completions doğrulandı.
- Geçici yerel SSH forward **127.0.0.1:58438 → Vast 127.0.0.1:18080** kullanıldı. Yerel model env yardımcı dosyası `/tmp/sales-progressive-model-env.sh` yalnız test DB/Redis ve bu model seçicisini içerir; üretim env değildir.
- `kasnak-gpu-ve-e2e-yay-n/automation.toml` yeniden okunup **PAUSED** doğrulandı. Eski staging/ZIP kilidi değişmedi. Windows/üretim dosyaları, DB ve `.env` değiştirilmedi. GPU'nun çalışması eski paketi dağıtma izni değildir.

### Gerçek modelin bulduğu ve düzeltilen sorunlar

1. Genel şirket runtime JSON şemasındaki `uniqueItems` vLLM grammar tarafından 400 ile reddediliyordu. Yalnız grammar ipucu kaldırıldı; `parse_customer_reply` duplicate fact ID'lerini bağımsız olarak reddetmeye devam eder. **76 runtime testi** geçti. Kasnak semantik dalı etkilenmiyordu; iki sektör kontrolü bu açığı yakaladı.
2. Planner şirket bilgisi yerine şirket hesap listesini, açık müşteri yerine genel kişi türünü ve role kodu yerine Türkçe etiketi seçebiliyordu. Genel ayrımlar ve rol kodları prompt'ta açıklaştırıldı. `format=text` teknik kodu yanlışlıkla literal metin filtresine takılıyordu; sınırlı semantik doğrulama düzeltildi. İçerik dayanağı/rol izni gevşetilmedi.
3. İlk gerçek builder denemesi verilen üretim bilgisini atlayıp yalnız şirket/agent metadatasını önizledi. Kabul script'i bunu başarısız saydı. Builder prompt'u her açık iş bilgisini facts'e koyacak, yalnız reply içinde bırakmayacak şekilde netleştirildi. Sonraki gerçek API denemesi geçti. Son builder değişikliği ardından **21 ilgili backend testi** geçti.

### Gerçek kabul kanıtları

- `verify_company_models.py`: düzeltilmiş çalıştırma **22/22 passed**, gerçek qwen3.8-27b; çıktı `/tmp/progressive-real-model-acceptance-v2.txt`. İlk başarısız çıktı `/tmp/progressive-real-model-acceptance.txt` başarı değildir.
- İki farklı sektör aynı soruya kendi literal bilgisiyle yanıt verdi. Kasnak typo sorusu `plastic_pulley_performance` seçti; fiyat ve stok resolution'ları `unavailable` ve doğru ürün subject'iyle döndü. Verifier bu özel sınırları da kontrol edecek şekilde güçlendirildi; mevcut gerçek v2 çıktısı yeni koşullara karşı ayrıca değerlendirildi ve geçti (sadece response_source=model kontrolü yeterli sayılmadı).
- Yeni `scripts/verify_progressive_live_model.py --account-file /tmp/sales-workflow-e2e-account.json`: yalnız loopback API'ye izin verir ve sentetik kayıt oluşturur. Gerçek doğal yapılandırma niyeti, gerçek builder önizlemesi, onay öncesi draft değişmemesi, draft kabulü, literal müşteri yanıtı ve aynı test session'ında 4 mesaj geçmişi geçti. Çıktı `/tmp/progressive-live-api-model-v2.txt`; ilk başarısız API çıktısı başarı değildir. Yayın/Meta çağrısı yok.
- Gerçek Chromium `progressive_real_model.py`: **8 kontrol**; hiç guided action veya model fixture kullanmadan gerçek Qwen ile kişi açma, aynı kartı doğal mesajla düzeltme, e-posta koruma, geçmişten açma, tek composer, mobil taşma/JS hatası yok. `/tmp/progressive-real-model-browser.txt`; görsel `/tmp/sales-workflow-real-qwen-mobile.png` incelendi.
- Model isteklerini bu tek-sequence sunucuya paralel yükleme; kabul testlerini seri çalıştır. Önceki kısa tanı çalışması dışında v2 kabul seridir.

### Kalan teslim kapsamı

Inbox/konuşma ayrıntıları/manual cevap/bot devamı; teklif ve teknik talep kayıtları; doğal outbox yolunu ortak workflow'a taşıma; geri alma/sahip yeniden davet/kişi mükerrer ayrıntısı; daha geniş durum galerisi/erişilebilirlik ve eski platform browser testlerinin yeni UI'ye taşınması. Eski bekleyen onay ile yeni aktif workflow aynı sohbetteyken doğal onayın hedef seçimini ayrıca doğrula. Ardından yeni paket/manifest, deployment kapıları ve yeni kullanıcı WhatsApp mesajıyla gerçek Meta kabulü. Gerçek modelin geçmesi bu kalanları tamamlanmış yapmaz; hedef aktiftir.

Tur sonunda yalnız bu görevin API 58010, web 53010 ve geçici SSH forward 58438 süreçleri kapatıldı. Vast instance/model servisi çalışır durumda bırakıldı; sonraki gerçek model kontrolleri için aynı instance'a loopback SSH forward yeniden açılabilir. Yeni kiralama yasağı ve PAUSED dağıtım kilidi sürer.

## Sekizinci uygulama turu — ortak gelen kutusu ve kalıcı manuel yanıt

- Gelen kutusu artık `records:inbox` listesidir. Tenant filtreli ad/şirket/telefon araması ve sayfalama; seçilen konuşmadan ortak `conversation`, `reply`, `resume_bot` kartları açılır. Eski model `workspace:inbox` niyeti de normalize edilir. Konuşma mesajları ve bot bekleme durumu ortak kayıtlarda görünür. İptal edilmiş konuşma polling ile yeniden açılmaz.
- Manuel yanıt önce alıcı/metin önizlemesi ister; geri dönmek veya alan değiştirmek eski önizlemeyi temizler. İzleyici okuyabilir; yanıt ve bot devamı güncel agent rolü ister. Tenant dışı konuşma, opt-out ve kapanmış 24 saat penceresi kanonik sınırda engellenir.
- `ConversationService.send_free_form` isteğe bağlı `prepared` callback'i sayesinde workflow, değişmez işlem makbuzu ve SENDING mesajını tek commit ile dış POST öncesinde kaydeder. Workflow adapter dış gönderimde TurnTransaction kullanmaz. Servis commit'leri sonrası private-user RLS geri kurulur ve sohbet kilidi yeniden alınır. Aynı işlem kimliği ilk makbuzu döndürür; ikinci POST yapmaz.
- Manuel gönderim callback etiketi `workflow-manual:<message-id>` ile erken teslimat bildirimi doğru tenant mesajına bağlanır. Mesaj kilitli yeniden okunarak erken webhook JSON'u korunur. Ağ hatasında doğrulanmış sent/delivered/read varsa belirsiz sayılmaz. Kanıt yoksa ambiguous, tekrar gönderim engelli; GET/polling sonraki teslimat kanıtını yansıtır. İşlem completed durumu teslim edildi anlamına gelmez: accepted/ambiguous/failed ayrıdır, UI belirsiz/başarısız sonuçta “Sonuç kontrol edilmeli” gösterir.
- Bot devamı kanonik DB-only resume yoludur; onay/receipt/audit aynı işlemde, önceki müşteri mesajını yeniden çalıştırmaz. Gönderim halen kanonik tek POST sınırıdır; bu tur hiçbir gerçek Meta mesajı gönderilmedi.

### Doğrulama

- Tam backend paketi **468 passed / 0 skipped**: `/tmp/progressive-inbox-full-tests.txt`. Bundan sonra eklenen tenant/rol/opt-out ve önizleme temizliği testleri dahil son inbox paketi **7 passed**: `/tmp/progressive-inbox-final-tests.txt`. Tam paket bu son eklemelerden sonra tekrar çalıştırılmadı. Ruff tüm src/scripts/tests temiz, mypy **116** kaynak dosyasında temiz. Next üretim build başarılı: `/tmp/progressive-inbox-web-build.txt`.
- Yeni `tests/test_progressive_inbox.py`: ayrı DB bağlantısından dış çağrı öncesi mesaj/workflow/receipt kalıcılığı; accepted, timeout ambiguous ve erken delivered + timeout; aynı ID yeniden deneme; belirsiz sonrası yeni gönderim engeli; kayıt→konuşma→bot devamı; replay yok; iptal kalıcılığı; 24h ve opt-out; tenant izolasyonu/güncel rol. Transport tamamıyla mock.
- Gerçek Chromium **10 kontrol**: `/tmp/progressive-inbox-browser-v3.txt`, script `apps/web/e2e/progressive_inbox.py`. Sentetik fixture `/tmp/sales-inbox-browser-fixture.json`; oluşturucu `/tmp/seed-progressive-inbox.py` yalnız yerel test DB'ye uygulandı. Menü/arama/mesaj okuma/yanıt formu/açık gönderim önizlemesi/geri dönüş/tek composer/mobil taşma/JS hatası kontrolleri. Gönderim onayına tıklanmaz. İlk browser çalışması eski “Geri” test locator'ı yüzünden başarısızdı; başarı kanıtı v3'tür. `/tmp/sales-workflow-inbox-mobile.png` son düzeltme sonrası görsel incelendi.
- Aynı mevcut GPU'ya yalnız geçici loopback SSH forward açıldı. Gerçek Qwen `Gelen kutusunu aç` ve `Müşteri konuşmalarını görmek istiyorum` **2/2** ortak `records:inbox` açtı: `/tmp/progressive-inbox-real-model.txt`. Bu iki kontrol tam 22 senaryo suite'i yerine geçmez. Model/Windows üretim ayarı değiştirilmedi.

### Kalanlar ve devam

Doğal outbox yönlendirmesi, teklif/teknik talep kayıtları, rollback/yeniden davet/mükerrer kişi ayrıntıları, eski bekleyen onay + aktif workflow hedef seçimi, geniş galeri/erişilebilirlik/eski E2E geçişi ve son yeni paket + dağıtım + taze kullanıcı WhatsApp kabulü devam ediyor. Inbox gönderiminden sonraki DB hata kurtarması ve gecikmiş callback'in tarayıcıda uçtan uca gösterimi daha fazla kabul kapsamına alınabilir; mevcut at-most-once makbuz testi ağ kaybını kapsar. Hedef tamamlanmadı. Eski dağıtım ZIP'i kullanılmaz; otomasyon PAUSED kalır.

Tur sonunda yalnız görevün API 58010, web 53010 ve SSH forward 58438 süreçleri kapatıldı. GPU mevcut çalışır durumunda bırakıldı.

## Dokuzuncu uygulama turu — doğal WhatsApp gönderim niyetleri

- `workflow_intents.normalize` artık doğrulanmış `outreach` niyetini ortak outreach başlangıcına, `send_outreach/cancel_outreach/outreach_status` niyetlerini aynı türün complete/cancel/inspect eylemlerine çevirir. Yeni numaralar önce details formuna girer; direkt eski batch preview paneli açılmaz. Alıcı/amaç literal dayanağı dönüşümden önce korunur. Model izin kanıtı üretemez; şablon/izin/review/kanonik queue kontrolleri değişmez.
- `inspect` model niyeti kayıtlı workflow durumlarını okur; terminal kartlar da kapsanır, form adımı veya revision değişmez (teslimat senkronizasyonunda gerçekten yeni kanıt varsa normal sync değişikliği olabilir). HTTP mutasyon eylem sözleşmesine gereksiz yeni inspect düğmesi eklenmedi.
- Complete sözcükleri türüne bağlıdır: gönder/yolla/ilet outreach veya reply içindir; başka davet/kişi/yayın kartını onaylamaz. Gönderim onayı mesajı yeni telefon içeriyorsa model yanlışlıkla complete seçse bile clarify ile reddedilir. Yeni alıcı talebi ancak başlangıç önizlemesi üzerinden ilerler. Negatif gönderim ifadeleri reddedilmeye devam eder.
- Birden çok uygun outreach kartı varsa mevcut seçim-required davranışı geçerlidir. Outreach iptali ilgisiz açık kişi formunu iptal etmez. Tarihsel eski batch düğme endpoint'leri korunur; eski batch hazırlama fixture'ı `test_chat_outbound.prepare` içinde yalnız scoped normalize bypass kullanır. Yeni doğal HTTP yolunu `test_workflow_intents.py` ayrıca kanıtlar; tarihsel fixture sonucu yeni UI kabulü değildir.

### Kanıtlar

- Odaklı routing + tarihsel queue/worker suite **33 passed**: `/tmp/progressive-natural-outreach-tests.txt`.
- Tam backend **474 passed / 0 skipped**: `/tmp/progressive-outreach-routing-full-tests.txt`. Son yeni-telefon onay koruması eklendikten sonra routing suite **21 passed**: `/tmp/progressive-outreach-routing-final-tests.txt`; tam paket bu son eklemeden sonra tekrar çalıştırılmadı. Ruff src/scripts/tests temiz; mypy **116** kaynakta temiz.
- Aynı mevcut Qwen üzerinde seri **4/4 gerçek niyet**: başlangıç literal iki telefon, gönderme, inspect, iptal. `/tmp/progressive-natural-outreach-real-model.txt`; script `/tmp/verify-natural-outreach-model.py`. Bu yalnız plan/normalize kabulüdür; Meta çağrısı yoktur.
- Gerçek API + gerçek Qwen + Chromium **8 kontrol**: `apps/web/e2e/progressive_natural_outreach.py`, `/tmp/progressive-natural-outreach-browser.txt`. Gerçek doğal başlangıç, alıcı korunması, eski cards dizisinin boş olması, durum sorgusunda revision korunması, doğal iptal, tek composer, mobil taşma ve JS hatası yok. Test şablon okuma/queue aşamasına geçmez; Meta hiç çağrılmaz. `/tmp/sales-workflow-natural-outreach-mobile.png` görsel olarak kontrol edildi. Web kaynak kodu bu tur değişmedi, önceki doğrulanmış üretim build kullanıldı.

Doğal outbox yönlendirmesi tamamlandı; tüm hedef tamamlanmadı. Teklif/teknik talep kayıtları, rollback/yeniden davet/mükerrer kişi ayrıntıları, eski pending onay + aktif workflow hedef seçimi, kapsamlı galeri/erişilebilirlik/eski E2E geçişi ve yeni dağıtım + taze WhatsApp kabulü hâlâ açık. Sonraki öncelik teknik talep/teklif kayıtlarını ortak kayıtlara ve küçük eylem kartlarına taşımak. Üretim/Windows/GPU model yapılandırması değiştirilmedi, eski paket kullanılmadı. Yalnız görevin yerel API 58010, web 53010 ve geçici SSH forward 58438 süreçleri tur sonunda kapatıldı; mevcut GPU çalışır bırakıldı.

## Onuncu uygulama turu — teknik talep ve teklif kayıt listeleri

- Ortak `records` sözleşmesine `requests` ve `quotes` kategorileri, durum ve bugün/tüm tarihler filtreleri eklendi. Liste 20 kayıt/sayfa; filtre/arama/asistan değişince açık page verilmediyse ilk sayfaya döner.
- Eski `find_requests` sorgusu `request_query` olarak ayrıldı; tarih dilimi, Türkçe ad normalizasyonu, telefon/UUID araması ve tenant koşulları iki yolda aynı kaynaktan gelir. Eski yolun 51 satırlık sınırı korunurken ortak listede gerçek count + offset vardır. Liste sorgusu model Intent uzunluk kısıtına bağlanmaz; izin verilen 160 karakterlik UI araması çalışır.
- `quotes` yalnız object türünde confirmed_snapshot ve answers taşıyan müşteri onaylı teknik talepleri listeler. Snapshot içeriği mevcut değişebilir answers'a tercih edilir. Her satırda “Müşterinin teknik talebi; fiyat veya uygunluk onayı değildir.” açıklaması vardır; fiyatlandırılmış teklif varmış gibi sunulmaz.
- Satırdan tenant kontrolüyle ortak müşteri konuşmasına geçilir. Yeni doğal `search` ve `quotes` niyetleri status/today/target korunarak ortak liste başlangıcına normalize edilir. `action:Teknik talepler` ve `action:Teklif talepleri` guided adları da aynı kategorileri açar.
- Talebe özel atama/not/durum/dosya/eksik bilgi eylemleri henüz ortak detay kartına taşınmadı. Eski seçili talep yolları bu tur kaldırılmadı; ortak listeden tek sonuç çıksa da sessiz mutation hedefi atanmaz. Bu bölümün tamamlandığını iddia etme.

### Kanıt ve sıradaki iş

- Tam backend suite **478 passed / 0 skipped**: `/tmp/progressive-request-full-tests.txt`. Son 160 karakter sorgu düzeltmesi ve ayrı tenant kontrolü sonrası **3 ilgili test passed**: `/tmp/progressive-request-final-tests.txt`. Tam suite bu son eklemeden sonra tekrar çalıştırılmadı. Önceki uyumluluk + liste paketi **12 passed** `/tmp/progressive-request-compat-tests.txt`.
- Yeni `test_workflow_requests.py`: 25 kayıtla sayfalama/örtüşme yok, tarih filtresi ilk sayfaya dönüş, onaylı snapshot, yalnız onaylı teklif talepleri, durum doğrulaması, konuşmaya geçiş, keyfi/başka tenant kimliği reddi, doğal listelerin eski cards üretmemesi ve uzun UI araması. Dış transport yok.
- Ruff src/scripts/tests temiz; mypy 116 kaynakta temiz (son değişiklik Any sorgu taşıyıcısı ve test eklentisidir). Web/model/Meta bu tur ayrıca çalıştırılmadı; backend testlerini browser kabulü sayma. Üretim, GPU, otomasyon ve dağıtım paketi değiştirilmedi; yerel sunucu/SSH süreçleri başlatılmadı.
- Sıradaki öncelik: request detay ve atama/not/status akışlarını ortak kartlara, canonical apply_review revizyon/yetki/audit sınırına bağlamak; ardından dosya/eksik alan ve doğal seçme yolları + browser/gerçek model kabulü. Önceki rollback/yeniden davet/mükerrer kişi, onay hedef seçimi, galeri/erişilebilirlik/eski E2E ve final yeni paket/dağıtım/taze WhatsApp kapıları hâlâ açık. Hedef aktiftir.

## On birinci uygulama turu — talep durum/atama/not kartı

- Yeni `request_update` workflow, `workflow_requests.py` üzerinden durum değiştirme, sorumlu atama/kaldırma ve iç not eklemeyi ortak form → değişiklik önizlemesi → açık onay → sonuç yoluna bağlar. Request kimliği liste düğmesinden gelir; alanlar kontrollüdür. İşlem türüne göre yalnız ilgili alan görünür ve zorunludur.
- Teknik talep/teklif kayıtlarında agent ve üstü güncel rollere durum/atama/not eylemleri açılır; draft için düğmeler sunulmaz. Doğrudan draft tamamlama canonical `apply_review` tarafından da reddedilir. Viewer listeyi okuyabilir, mutasyon başlatamaz.
- Önizleme request_revision snapshot'ı taşır. Complete doğrudan DB-only canonical `apply_review` çağırır; aynı tenant/etkin güncel hesap, etkin yetkili assignee, izin verilen durum geçişi, row lock + revizyon ve audit/internal_notes kuralları korunur. Workflow receipt ve mutasyon tek commit'tedir; harici mesaj servisi çağrılmaz. Geri/alan değişimi eski changes önizlemesini temizler. No-op ayrı `no_change` sonucudur.
- Testler status→in_review, assignee, stale note 409→yeniden önizleme, aynı operasyon kimliğiyle tek audit/note, boş notun alan hatası, draft reddi, confirmed_snapshot ve answers'ın değişmemesini doğrular. Taleple ilgili önceki liste/tenant/sayfalama testleri de aynı dosyada kalır.
- Tam backend **481 passed / 0 skipped** `/tmp/progressive-request-actions-full-tests.txt`. Son no_change sonucu eklemesinden sonra ilgili **5 passed** `/tmp/progressive-request-actions-final-tests.txt`; tam suite bu son eklemeden sonra tekrar çalıştırılmadı. Ruff src/scripts/tests ve mypy **117** kaynak dosyasında temiz.
- Web/model/Meta bu tur çalıştırılmadı; gerçek browser talep akışı, doğal talep seçme/atama/not/status dönüşümü ve ortak detay/dosya/eksik alan gösterimi henüz açık. Sonraki tur bu akışın kullanıcı yolunu tamamlayıp browser/model kabulü yapmalı. Önceki tüm kalan teslim kalemleri geçerlidir; hedef tamamlanmadı. Üretim, GPU, dağıtım ve otomasyona dokunulmadı; yerel servis açılmadı.

## On ikinci uygulama turu — talep browser kabulü ve filtre etiketleri

- Yeni `apps/web/e2e/progressive_requests.py` gerçek yerel API/DB ile guided teknik talep listesi → isim arama → seçili kayıttan iç not → geri/düzeltme → açık onay → yeniden listeden durum değişikliği → güncel durum akışını çalıştırır. Gerçek model testi değildir; dış mesaj çağrısı yoktur.
- İlk browser denemeleri zorunlu yıldızlı label, önceki bekletilmiş kartın asenkron seçilmesi ve birden fazla/kapalı satırdaki durum metni gibi test seçici sorunlarında başarısızdı. Son script yeni turn yanıtının workflow kimliğine ve seçili record details'ına bağlıdır. Başarı kanıtı **v6** `/tmp/progressive-request-browser-v6.txt`: **9 kontrol**; tek composer, mobil yatay taşma ve JS hata kontrolleri dahil.
- Ayrı veritabanı okuması durumun `in_review`, notun tam bir kez ve müşteri onaylı snapshot'ın değişmeden kaldığını doğruladı: `/tmp/progressive-request-browser-db.txt`. Sentetik fixture `/tmp/sales-request-browser-fixture.json`; oluşturma ve kontrol script'leri `/tmp/seed-request-browser.py` ve `/tmp/verify-request-browser-db.py`. Yalnız yerel test tenant'ı.
- Görsel incelemede raw `in_review` kodu Türkçe etiketle değiştirildi. Ortak select renderer, sunucunun boş değer için verdiği “Tümü” seçeneği varsa ikinci “Seçin” seçeneği üretmez. Request listesinde eksik filtre varsayılanları status boş/today false olarak doldurulur. Son mobil görsel `/tmp/sales-workflow-requests-mobile.png` incelendi: “Tümü” ve “Tüm tarihler” görünüyor.
- Son değişiklik sonrası ilgili **5 backend testi passed** `/tmp/progressive-request-display-tests.txt`; Ruff temiz, mypy **117** kaynakta temiz. Next üretim build başarılı `/tmp/progressive-request-web-build.txt`; v6 bu build ile çalıştı. Bu tur tam backend suite ve gerçek model kabulü yeniden çalıştırılmadı.
- Yerel API 58010 ve web 53010 tur sonunda kapatıldı. GPU/Windows/üretim/otomasyon/release değişmedi, SSH forward açılmadı. Kalanlar: doğal talep seçimi ve request update niyetleri, detay/dosya/eksik bilgiler; rollback/yeniden davet/mükerrer kişi; eski pending onay hedef seçimi; galeri/erişilebilirlik/eski E2E; final yeni paket/dağıtım/taze WhatsApp kabulü. Hedef aktiftir.

## On üçüncü uygulama turu — doğal talep düzenleme niyetleri

- Router, doğrulanmış `status/assign/note` niyetlerini `workflow_requests.route_intent` ile ortak request_update başlangıcına çevirir. Doğal ilk komut doğrudan kayıt mutasyonu yapmaz; alanlar → inceleme → açık kaydetme gerekir. Literal argüman/negatif yazma doğrulamaları planner'da dönüşümden önce kalır.
- Açık hedef aynı tenant'ın canonical request sorgusunda tekil çözülür. Hedef verilmemişse yalnız aktif request_update veya toplamı tam 1 olan aktif talep listesi kullanılabilir. Tamamlanmış kart/başka aktif iş/çoklu sonuçtan sessiz hedef seçilmez; ortak liste + “İşlem yapılmadı; ... seçin” döner. Belirsiz komut pending olarak saklanıp sonraki seçime otomatik uygulanmaz.
- Sorumlu e-posta/tam ad ile aynı tenant'ın etkin, review yetkili hesaplarında tekil çözülür. UUID modelden kabul edilmez; sunucu çözer. Tekil değilse assignee boş bırakılır, ortak seçiciye yönlendirir. Doğal not/durum alanları literal/doğrulanmış değerlerle korunur.
- Backend testleri açık UUID durum talebi → ortak details, onay öncesi request değişmemesi; continue/complete; bitmiş karttan zamirle sessiz seçim olmaması; etkin yerel e-posta çözümü ve bulunamayan sorumluda boş seçici + DB ataması yapılmaması kontrollerini ekledi.
- Tam backend **483 passed / 0 skipped** `/tmp/progressive-natural-request-full-tests.txt`; ilgili **7 passed** `/tmp/progressive-natural-request-tests.txt`. Ruff temiz ve mypy **117** kaynak dosyasında temiz. Web kaynakları değişmedi; browser bu tur yeniden çalıştırılmadı.
- Aynı mevcut Qwen + gerçek loopback API ile **3 aşama** geçti: UUID + “Teknik ekip arayacak” notu ortak forma; “Devam et” aynı workflow'u review'a; “Değişikliği kaydet” explicit canonical commit. `/tmp/progressive-natural-request-real-api.txt`; script `/tmp/verify-natural-request-api.py`. Yalnız önceki sentetik yerel request fixture kullanıldı; gerçek Meta çağrısı/yayın yok.
- Yerel API 58010 ve geçici SSH forward 58438 tur sonunda kapatıldı; mevcut GPU çalışır kaldı, model/Windows/üretim/otomasyon/release değişmedi.
- Kalan: talep detay/dosya/eksik alan gösterimi ve doğal seçme/okuma yolları; rollback/yeniden davet/mükerrer kişi ayrıntıları; eski pending onay + aktif workflow hedef belirsizliği; galeri/erişilebilirlik/eski E2E; tüm kapsamın son kabulü ve yeni paket/dağıtım/taze WhatsApp. Hedef tamamlanmadı.

## On dördüncü uygulama turu — eski preview / yeni workflow onay hedefi

- Router işlem öncesi güncel workflow görünümünü bir kez okur ve planner'a aynı görünümü verir. Eski workspace pending_operation ile aktif ortak workflow birlikteyken genel “Onayla / Bunu onayla / Değişikliği kaydet / Uygula / Kabul et” türü onaylar modelin workspace-confirm veya workflow-complete seçimine bakılmaksızın selection_required döndürür. İki kayıt da tüketilmez/değişmez; turn retry aynı yanıtı verir.
- Explicit server operation-id taşıyan guided kart onayı ve ortak action endpoint'i bu belirsizlik değildir. “Kişiyi kaydet”, “Taslağı yayınla” gibi hedef belirten sözler guard kapsamına alınmaz; mevcut planner/role/review kontrollerinden geçer. Negatif ifade guard'ın işi değildir; mevcut planner reddi korunur.
- Gerçek PostgreSQL/RLS HTTP testinde eski davet önizlemesi + yeni hazır kişi kartı oluşturuldu; modelin her iki yanlış/hedef seçimi olasılığı denendi. Genel onay ikisini de korudu, ardından her iki kartın ayrı açık onayı başarılı oldu. Bu dış e-posta göndermez; sentetik davet token'ı üretir.
- İlgili routing/guard paketi **29 passed** `/tmp/progressive-approval-target-final-tests.txt` (ilk HTTP eklemesiyle 23 passed `/tmp/progressive-approval-target-tests.txt`). Ruff src/scripts/tests ve mypy **117** kaynakta temiz. Bu tur tam suite/browser/gerçek model tekrar çalıştırılmadı; davranış modelden bağımsız sunucu kontrolüdür. Yerel servis/GPU/üretim/release/otomasyon değişmedi.
- Bu belirli onay belirsizliği maddesi karşılandı. Hâlâ talep detay/dosya/eksik alan ve doğal okuma/seçme, rollback/yeniden davet/mükerrer kişi, galeri/erişilebilirlik/eski E2E, son kapsamlı kabul ve yeni paket/dağıtım/taze WhatsApp kapıları var. Hedef aktiftir.

## On beşinci uygulama turu — talep ayrıntısı, eksikler ve dosyalar

- Ortak records kategorisi `request_details` eklendi; listeden “Talep ayrıntıları” açar. Onaylı müşteri özeti, durum, son 20 iç not, eksik alanlar, teknik/fiyat onayı ayrımı ve dosya metadatası aynı kayıt görünümündedir. Talep kaydından konuşma/status/atama/not eylemleri çalışır. Sonuçlar 20 satırlık sayfalarda gösterilir (dosya metadatası şu an önce toplanıp sayfalanır; dosya içerikleri ORM deferred kalır).
- `missing_fields` ortak işlevi eski service ve yeni details tarafından kullanılır. Confirmed snapshot answers önceliği, skip_if ve unknown/drawing eksikliği korunur; primitive skip_if yanıtında dict çağrısı yapılmaz.
- Record sözleşmesinde sunucunun verdiği request_id/file_id içeren opsiyonel file nesnesi; ortak frontend yalnız mevcut `/api/platform/selection-requests/<request>/files/<file>` yetkili download yoluna link kurar. Keyfi dış URL kabul edilmez. Viewer'a indirme eylemi verilmez, kanonik download kendi güncel yetki/RLS doğrulamasını yapar.
- Doğal select/summary/missing/files/conversation niyetleri tekil açık hedef veya aktif request_update/request_details/tek kayıtlı request listesi üzerinden çözümlenir. Ayrıntı istekleri ortak details; konuşma ortak conversation olur. Belirsiz hedefte mevcut selection-required listeye dönüş sürer. Legacy delivery okuma niyeti bu dönüşüme henüz dahil değil; eski seçili request context'ine dayanabildiği için ortak talep bağlamıyla ayrıca uyumlandırılmalı.
- Gerçek PG/RLS HTTP testi snapshot özeti + eksik Çap + iç not + dosya metadata + canonical endpoint'ten birebir dosya byte'ları + doğal missing/conversation ortak akışlarını doğruladı. İlgili **8 passed** `/tmp/progressive-request-details-tests.txt`; tam suite **492 passed / 0 skipped** `/tmp/progressive-request-details-full-tests.txt`. Ruff temiz, mypy 117 kaynakta temiz.
- Next üretim build başarılı `/tmp/progressive-request-details-web-build.txt`. Gerçek Chromium **11 kontrol** `/tmp/progressive-request-details-browser.txt`: önceki liste/not/status akışı + ortak details notu + gerçek authenticated download ve birebir içerik kontrolü; tek composer, mobil taşma/JS hatası yok. Script `apps/web/e2e/progressive_requests.py`; sentetik fixture üreticisi `/tmp/seed-request-browser.py` artık ayrı synthetic inbound Message + SelectionFile ekler. `/tmp/sales-workflow-requests-mobile.png` incelendi. Meta/model bu tur çağrılmadı.
- Yalnız bu görevin API 58010 ve web 53010 süreçleri kapatıldı. GPU/Windows/üretim/otomasyon/release değişmedi. Hedef tamamlanmadı; kalan başlıca işler delivery ortak bağlamı, rollback/yeniden davet/mükerrer kişi, galeri/erişilebilirlik/eski E2E, son gerçek model/tüm kapsam kabulü ve yeni paket/dağıtım/taze WhatsApp.

## On altıncı uygulama turu — teslimat ortak bağlamı

- `workflow_inbox.delivery_summary` son tenant/konuşma outbound mesajından gerçek kanıtı okur: mesaj delivery_status, runtime audit veya outreach job; manual_send_state yalnız uygun belirsiz/sending/accepted açıklaması sağlar. Manuel `sent` kaydı Meta accepted demektir, delivered sayılmaz. Eski service delivery ve yeni request_details aynı hesaplamayı kullanır.
- Request details “Son mesaj” bilgisi gösterir. Konuşma output'u bot bekleme durumu yanında son mesaj teslimatını gösterir. Doğal delivery niyeti aktif request_details/request_update/tekil liste bağlamından details'a; aktif conversation bağlamından aynı konuşmaya gider. Eski selected_request_id'ye bağımlı doğal delivery yolu kaldırıldı; açık hedef canonical tenant sorgusuyla çözülür.
- Gerçek PG/RLS testi accepted → delivered kanıt değişimi ve doğal “Son mesaj ulaştı mı?” sorusunun hem talep hem konuşma bağlamında ortak karttan doğru sonucu okuduğunu doğrular. İlgili request+inbox suite **16 passed** `/tmp/progressive-delivery-context-tests.txt`; tam backend **493 passed / 0 skipped** `/tmp/progressive-delivery-context-full-tests.txt`. Ruff ve mypy **117** kaynakta temiz. Web/model/browser/Meta bu tur ayrıca çalıştırılmadı; yerel servis/GPU/üretim/release değişmedi.
- Sıradaki yönetim önceliği rollback: mevcut `AgentService.rollback_to` agent lock altında eski içeriği yeni LIVE versiyona clone eder, geçmişi yeniden yazmaz ve audit tutar. Ortak workflow adapter'ında hedef sürüm + önceki LIVE/revizyon önizlemesi ve tekrar onay kontrolüyle kullanılmalı. Henüz uygulanmadı. Yeniden davet/mükerrer kişi, galeri/erişilebilirlik/eski E2E, son tüm kapsam/gerçek model kabulü ve yeni paket/dağıtım/taze WhatsApp da açık. Hedef aktiftir.

## On yedinci uygulama turu — ortak sürüm geri alma

- `rollback` workflow kind ve agent/version seçicileri eklendi. Sürümler listesinden yayımlanmış/arşiv sürüm “Bu sürüme dön” ile açılır; draft seçime sunulmaz, hedef otomatik seçilmez. Güncel manager+ yetkisi gerekir.
- Prepare agent lock altında hedef sürüm revizyonu, mevcut LIVE kimliği + revizyonu ve içerik değişikliklerini snapshot'lar. Açık onayda bu kanıtlar yeniden kontrol edilir. Canonical `rollback_to` DB-only TurnTransaction üzerinden yeni LIVE clone oluşturur; eski row'lar/audit korunur, receipt ve etkiler atomiktir.
- Gerçek PostgreSQL HTTP testi liste eylemi → review; canonical başka rollback ile LIVE değişimi → 409; geri/yeni preview → tek yeni sürüm; aynı client_operation_id → aynı receipt ve toplam tam üç sürüm/tek LIVE/orijinal archived kanıtlarını doğruladı. İlgili workflow suite **16 passed** `/tmp/progressive-rollback-tests.txt`.
- Tam backend **494 passed / 0 skipped** `/tmp/progressive-rollback-full-tests.txt`. Son doğal onay/olumsuzluk düzeltmelerinden sonra routing suite **33 passed** `/tmp/progressive-rollback-phrase-final-tests.txt`; tam suite bu son eklemelerden sonra tekrar çalıştırılmadı. Ruff temiz ve mypy 117 kaynakta temiz.
- Gerçek Qwen ilk denemede “geri almayı onayla” sunucu negatif substring kontrolüne takıldı. “geri alma” kelime sınırıyla ve -yın/-yınız biçimleriyle ayrıldı; “geri almak istemiyorum” da reddedilir. Başarılı gerçek ikinci çalıştırma `/tmp/progressive-rollback-real-model-v2.txt`: hedef uydurmadan start + mevcut review için complete **2/2**. İlk `/tmp/progressive-rollback-real-model.txt` başarı değildir. Script `/tmp/verify-rollback-model.py`; bu plan kontrolüdür, gerçek yayın/Meta çağrısı yapmaz.
- Geçici SSH forward 58438 kapatıldı; aynı GPU/model çalışır kaldı. API/web/Windows/üretim/release/otomasyona dokunulmadı. Yeni rollback browser akışı ve durum galerisi henüz doğrulanmadı; son kapsam kabulüne dahil edilmeli.
- Sıradaki kalemler: yeniden davet ve mükerrer kişi ayrıntıları, rollback dahil kalan browser/galeri/erişilebilirlik/eski E2E geçişi; son tüm kapsam ve gerçek model kabulü; yeni paket/dağıtım/taze WhatsApp. Hedef tamamlanmadı.

## On sekizinci uygulama turu — sahip yeniden daveti ve mükerrer kişi ayrıntıları

- Yeni `owner_invite` workflow, platform yöneticisine şirket seçici + sahip e-postası → önizleme → açık bağlantı hazırlama → invitation_ready sonucu sağlar. Şirketler kaydından “Sahip daveti hazırla” aynı akışı açar. Canonical `platform.invite_owner` DB-only TurnTransaction ile çağrılır; target tenant işleminden sonra tenant ve private-user context'leri finally içinde geri kurulur. Rol sabit tenant_owner; e-posta gönderilmez, üyelik kabul edilmeden oluşmaz.
- Aynı action ID aynı token/receipt'i döndürür; bilinçli yeni workflow yeni davet token'ı oluşturabilir. Mevcut kabul edilmiş kullanıcı için canonical conflict korunur; eski token iptal davranışı değiştirilmedi. Doğal planner'a platform sahibini yeniden davet etmenin normal ekip daveti olmadığı ve tenant kimliği uydurulmaması gerektiği eklendi; gerçek model bu yeni niyet için bu tur ayrıca denenmedi.
- Mükerrer contact sonucunda salt kimlik dizisi yanında eşleşen tenant Lead kayıtlarının ad/şirket/iletişim bilgileri ortak records olarak gösterilir. Completed contact kartı bu salt okunur kayıtları render eder. Birleştirme/üzerine yazma/opt-in oluşturma/ek gönderim yoktur.
- İlgili progressive suite **17 passed** `/tmp/progressive-owner-duplicate-tests.txt`. Owner test şirket listesi→launch→review→receipt→private session context→farklı kullanıcı 404→bilinçli yeni davette farklı token→hedef şirkette iki kabul edilmemiş owner invitation→tenant_owner başlatma 403 doğrular. Duplicate test somut isim ve e-posta kaydını kontrol eder.
- İlk tam suite, yeni testin invitations sayımında tenant filtresi olmaması nedeniyle önceki test şirketlerinin davetlerini de saydı (1 failed/498 passed). Invitations tablosunun auth için RLS'den istisna olduğu migration `2d7e4a9c1f03` ile uyumlu olarak test açık tenant koşuluyla düzeltildi. Başarı kanıtı ikinci tam suite **499 passed / 0 skipped** `/tmp/progressive-owner-duplicate-full-tests-v2.txt`; ilk çıktı başarı değildir.
- Ruff src/scripts/tests temiz; mypy **118** kaynakta temiz. Next üretim build başarılı `/tmp/progressive-owner-duplicate-web-build.txt`. Owner/duplicate/rollback yeni browser kabulü henüz yapılmadı. Bu tur yerel sunucu/SSH/GPU/Windows/üretim/otomasyon/release değişmedi.
- Sıradaki ana iş: bu üç yönetim yolunu browser'da doğrulamak, durum galerisi/erişilebilirlik ve eski E2E'leri yeni UI'ye uyarlamak; sonra son tüm kapsam/gerçek model kabulü ve yeni paket/dağıtım/taze WhatsApp. Hedef tamamlanmadı.

## On dokuzuncu uygulama turu — mükerrer kişi browser kabulü

- Yeni `apps/web/e2e/progressive_duplicates.py` gerçek yerel API/DB + Chromium ile ilk kişiyi kaydeder, aynı e-postayı farklı adla tekrar girer ve completed duplicate kartında orijinal ad/e-postayı doğrular. Kayıt salt okunur, tek composer, mobil taşma ve JS hata kontrolleri dahil **7 kontrol passed**: `/tmp/progressive-duplicate-browser-v3.txt`.
- İlk iki deneme test seçicilerinde başarısızdı (autosave yerine complete yanıtını bekleme; aynı e-postanın kapalı snapshot ve açık kayıtta bulunması). Son script yalnız action=complete yanıtını ve açık Kayıt listesi bölümünü kontrol eder. Önceki başarısız çıktılar başarı sayılmaz.
- `/tmp/sales-workflow-duplicate-mobile.png` görsel olarak incelendi. Uygulama kodu bu tur değişmedi; önceki başarılı production build kullanıldı. Model/Meta çağrısı yok. Yalnız görevün API 58010 ve web 53010 süreçleri kapatıldı. GPU/üretim/release/otomasyon değişmedi.
- Rollback ve owner invite browser kabulü, galeri/erişilebilirlik/eski E2E geçişi, son tüm kapsam/gerçek model ve yeni paket/dağıtım/taze WhatsApp hâlâ açık. Hedef aktiftir.

## Yirminci uygulama turu — rollback ve sahip daveti browser kabulü

- `apps/web/e2e/progressive_rollback.py`: gerçek yerel sentetik asistanın sürüm listesinden hedef seçimi → tam UUID korunması → yeni LIVE açıklaması → açık onay → yeni sürüm kimliği/sonuç; tek composer/mobil taşma/JS hata dahil **9 kontrol passed** `/tmp/progressive-rollback-browser.txt`.
- Ayrı API geçmiş okuması orijinal sürümün archived, tam bir yeni LIVE ve rolled_back_from_version=1 olduğunu doğruladı: `/tmp/progressive-rollback-browser-history.txt`. Fixture üreticisi `/tmp/seed-rollback-browser.py`, fixture `/tmp/sales-rollback-browser-fixture.json`, doğrulayıcı `/tmp/verify-rollback-browser-history.py`. Yalnız yerel test hesabında yapılandırma + yayın/rollback; gerçek müşteriye/Meta'ya etkisi yok.
- `apps/web/e2e/progressive_owner_invite.py`: şirketler menüsü/arama → seçilen şirket → sahip e-postası → açık önizleme → invitation_ready token/henüz kabul edilmedi sonucu; tek composer/mobil taşma/JS hata dahil **8 kontrol passed** `/tmp/progressive-owner-invite-browser.txt`. E-posta gönderilmedi. Ayrı sentetik yerel platform kullanıcısı `/tmp/seed-owner-browser.py` ile üretildi; fixture `/tmp/sales-owner-browser-account.json` 0600 izinlidir. Mevcut kullanıcı yetkileri değiştirilmedi.
- `/tmp/sales-workflow-rollback-mobile.png` ve `/tmp/sales-workflow-owner-invite-mobile.png` görsel olarak incelendi. Uygulama kaynakları değişmedi; önceki başarılı production build kullanıldı. Model çağrısı yok. Yalnız görevün API 58010/web 53010 süreçleri kapatıldı; GPU/Windows/üretim/release/otomasyon değişmedi.
- Mükerrer kişi, rollback ve owner invite yeni browser kabulü artık kanıtlandı. Kalan ana kapsam: durum galerisi/erişilebilirlik ve eski E2E geçişi; tüm gereksinimleri karşılayan son kabul/audit, gerçek model suite genişletmesi; yeni paket/dağıtım/taze WhatsApp. Hedef tamamlanmadı.

## Twenty-first implementation turn — gallery and accessible field semantics

- Expanded development-only shared card gallery from 7 to 17 states, including empty records, request details, draft review, rollback review, failed model test, queued outreach, ambiguous delivery, owner invitation, existing contact and cancellation.
- Required input/select/textarea controls now expose `aria-required`; decorative required stars are hidden from screen readers. Existing labels and error descriptions remain associated.
- Corrected review copy for rollback, owner invitation and request updates, which previously fell through to contact creation wording.
- Playwright verified all 17 cards, 390px and 1280px horizontal containment, label and required semantics, invalid-control error references, visible keyboard focus and absence of page errors. Screenshot: `/tmp/progressive-gallery-mobile.png` (before removing unrelated contact fields from the draft example). These disabled examples do not constitute interactive-form or full WCAG acceptance.
- Production gallery returned HTTP 404. Production build passed before gallery verification; final rebuild recorded in `/tmp/progressive-gallery-web-build.txt`. Local dev server stopped. No API, model, production deployment or external messages used in this turn.
- Full objective remains open: live form accessibility, legacy E2E migration, complete requirements audit and real-model acceptance, then fresh release/deployment acceptance remain pending.

## Twenty-second implementation turn — live accessibility and session contract migration

- Previous turn classified as progress: production code/gallery changed and browser/build evidence recorded.
- Added `apps/web/e2e/progressive_session.py`: real synthetic tenant login with production web, both expected HttpOnly/Secure/SameSite cookies, JavaScript isolation, six simultaneous refresh requests, refresh rotation, rejected cross-origin mutation, tenant platform-menu restriction, logout cookie removal and authenticated endpoint returning 401. All nine checks passed (`/tmp/progressive-session-browser.txt`).
- Extended `progressive_workflows.py` with actual input accessible-name/required semantics, server error description references, keyboard typing and visible focus. All twelve checks passed (`/tmp/progressive-accessible-workflows-v4.txt`), including the original real DB completion/history/mobile checks.
- Initial accessibility attempts exposed test issues: `get_by_label` exact matching included the decorative star, so the test now targets the textbox accessible name; keyboard typing must wait for the validating request to re-enable the field. One intermediate local script had a syntax typo. None of those failed attempts are counted as successes.
- Added `apps/web/e2e/README.md` mapping historical contracts to current scripts and explicitly listing missing migration: full new-company/invitation-acceptance/first-owner session, capacity/empty-home assertions, and historical card compatibility. Historical files remain available; current acceptance does not silently count old panel tests.
- No production application code changed this turn. Existing production build was used. No model or Meta requests were needed; only synthetic local test records were created. Local API/web stopped after tests. Full goal remains active.

## Twenty-third implementation turn — full company onboarding migration

- Previous turn classified as progress: new session test and live form evidence, plus explicit historical coverage mapping.
- Added `apps/web/e2e/progressive_provisioning.py`. Uses only synthetic local platform/owner accounts and actual browser/API/DB. Creates a new company from the common company list, explicitly approves the review, reads the actual clipboard invitation link, accepts it in an isolated browser context, logs in, verifies the owner role AND newly created tenant ID, creates the first assistant through common cards, and restores the result from conversation history. Mobile containment, single composer and JS errors checked.
- First run found a real UI defect: invitation form unconditionally required a company code although acceptance uses only the invitation token. Removed the company code control during invitation acceptance; normal login retains it. No API authorization changes.
- All 12 provisioning checks passed in `/tmp/progressive-provisioning-browser-v2.txt`. Screenshot `/tmp/progressive-provisioning-mobile.png` visually inspected. All 9 normal session/security regression checks passed in `/tmp/progressive-provisioning-session-regression.txt` after the fix. Production build passed (`/tmp/progressive-provisioning-web-build.txt`). No model/Meta/email calls.
- Updated browser migration map: company provisioning/acceptance now migrated. Capacity/empty-home legacy assertions and final comprehensive acceptance remain. Local API/web stopped; no deployment performed. Full goal remains active.

## Twenty-fourth implementation turn — capacity migration and completion audit index

- Previous turn: progress (fixed invitation acceptance and verified complete synthetic onboarding).
- Added `progressive_capacity.py`: real authentication, explicitly fixture-backed chat responses. Ten checks passed for empty home, on-demand capacity, no embedded navigation, desktop/mobile containment, visible single composer, separate Meta/local metrics, unavailable Meta shown as unavailable rather than zero, disconnected metrics hidden, and no JS errors. Evidence `/tmp/progressive-capacity-browser-v3.txt`; mobile screenshot visually inspected.
- First attempt depended on a retired suggestion button; migrated to the actual composer. Second attempt measured immediately after resize; added two animation frames for viewport state to render. Failed attempts not counted as passes.
- Updated browser migration map and added `docs/progressive-acceptance-audit.md` separating existing evidence from missing final gates. Comprehensive real-model rerun, focused owner-invite interpretation, historical rendering assessment and new deployment package/production acceptance are still open. The audit explicitly does not claim full completion.
- No application source changed this turn, no model/Meta calls, no deployment. Local API/web stopped. Goal remains active.

## Twenty-fifth implementation turn — actual Qwen acceptance and Windows tunnel repair

- Previous turn: progress (capacity migration and completion evidence index).
- Expanded `verify_company_models.py` from 22 to 25 cases with owner invitation (email present, missing values, reviewed explicit confirmation), checking no invented tenant/role/email.
- First full real-model run failed three cases. Captured synthetic raw model output showed invite fields incorrectly emitted as legacy top-level email/role, and ready owner invitation confirmation interpreted as start. Corrected planner instructions with explicit workflow_fields examples and ready-owner confirmation semantics; validation and grounding rules remain strict.
- Focused actual Qwen diagnostics then passed all three. Full second real-model run passed **25/25**, including two-sector isolation, Kasnak typo and unknown price/stock, chat operations and owner invitation: `/tmp/progressive-final-model-acceptance-v2.txt`. First failed run is not acceptance evidence.
- Full local PostgreSQL/Redis backend suite: **499 passed, zero skipped** (`/tmp/progressive-final-backend-tests.txt`) before the prompt-only correction; 33 focused intent regression tests passed afterward (`/tmp/progressive-final-intent-regression.txt`). Ruff clean and mypy clean in 118 source files after correction. Only warning is passlib's Python crypt deprecation.
- Incoming coordination from task 01a09ed3-567d-7c52-8d1f-6bbba9fa592d requested restoration of existing Windows model tunnel without new GPU, old package or automation activation. Windows scheduled task was running but port 11438 unavailable; log showed publickey rejection. Existing Windows public key was absent on the existing Vast host. Backed up authorized_keys and restored that public key with restrict, port-forwarding, permitopen=127.0.0.1:18080 and forced /bin/false command. Private key stayed on Windows. Removed an initially unsupported permitlisten=none option after unsuccessful authentication; final key restrictions are as listed. Existing task reconnected without modifying its script or restarting production app tasks.
- Windows localhost:11438 model list and real chat completion succeeded, returning qwen3.8-27b / READY: `/tmp/progressive-windows-model-check-v2.txt`. An intermediate SSH banner timeout was retried only after it was terminal; it is not model acceptance evidence.
- No Windows source/.env/config or old staged package deployed. No new GPU, no Meta send. Local SSH forward closed after verification; existing GPU/Windows tunnel left running. Full objective remains open for remaining final acceptance and fresh deployment.

## Twenty-sixth implementation turn — final live API/browser rerun and fresh package

- Previous turn: progress (25-case model acceptance, planner corrections and Windows tunnel repair).
- Current code passed `verify_progressive_live_model.py` through real local API/DB/Qwen: natural config intent, actual builder preview with no pre-acceptance write, explicit draft save, two actual customer model turns sharing persisted test history. `/tmp/progressive-final-live-api.txt`.
- Current production web build passed (`/tmp/progressive-release-web-build.txt`). Eight real Qwen mobile browser checks passed (`/tmp/progressive-final-real-model-browser.txt`): natural contact creation intent, literal fields, same-workflow correction, preserved email, restored history, one composer, containment, no JS errors.
- Added `verify_progressive_live_model.py` to package script's explicit release inclusions. Ruff passed for packaging change.
- New candidate ZIP `/tmp/sales-progressive-release-20260914-r1.zip`, **382 manifest files**, SHA-256 `1e8ad169e0cf9e22cb9e65738887ef7b941a1933e5830d70d8c85c30dba4f5c8`. Verified every archived file hash, exact manifest/archive membership, no duplicate names, required current planner/migration/API verifier/build marker, no environment/key/node_modules inclusions, ZIP CRC integrity. This is a fresh candidate, not a deployed release. The old company release ZIP/staging remains forbidden.
- No production files or Meta messages changed this turn. Local API/web/model-forward stopped; existing remote GPU and Windows tunnel kept. Still open: final deployment readiness audit, backup/source drift checks, fresh package deployment and production/fresh WhatsApp acceptance. Goal remains active.

## Twenty-seventh implementation turn — Windows staging and fresh backup

- Previous turn: progress (real API/browser rerun and fresh verified package).
- Read-only production inventory confirmed API/worker/recovery/review/tunnel tasks running, old active web `admin-chat-simple-20260909-r2`, Next 15.0.3 matching candidate, ample free disk, and existing IIS route gaps described in deployment plan.
- Recorded production app/.env SHA-256 `b74d7d5c522917c83dcc2348fe56a746bdb946e7cb740bfda88dda30292e28dd`. No private environment contents transferred or changed.
- Byte-exact transfer of fresh r1 ZIP to `C:/sites/ashiraai/staging/progressive-20260914-r1.zip`, matching `1e8ad169e0cf9e22cb9e65738887ef7b941a1933e5830d70d8c85c30dba4f5c8`; all 382 manifest hashes verified before extraction to sibling staging directory. Old staged package untouched.
- Fresh server-only backup `C:/sites/ashiraai/backups/progressive-20260914-140521`: 357 source/web/launcher/IIS/private-env files in files.zip with source-manifest.json; database.dump 349468 bytes, SHA-256 `78ac3bbb79f1ef82484969a1fd1aa23fb0693a3eb468c97acdcdabe55f84e40e`, pg_restore --list succeeded. Credentials only in subprocess environment. Pointer saved server-side at staging/progressive-backup-pointer.txt. Evidence `/tmp/progressive-backup-result.txt`.
- Staged candidate Python compilation, actual src.main import (105 routes), CompanyAgentConfig validation and single Alembic head f56ef78ab90c passed on Windows using existing installed dependencies: `/tmp/progressive-windows-candidate-validation-v2.txt`. First staging import lacked public/media (runtime data intentionally absent from release); confirmed production media directory exists, then created only an empty staging media directory for import validation.
- No active source, IIS rule, runtime environment, LIVE config or DB schema modified; no production tasks stopped. Next step is pre-cutover source hash comparison, then authorized fresh deployment/migrations/runtime binding/IIS changes with task isolation and postflight. Backup is a pre-deployment snapshot, not proof of a quiesced cutover. Goal remains active.

## Twenty-eighth implementation turn — controlled production deployment

- Previous turn: progress (fresh backup and Windows compatibility validation).
- Pre-cutover source/.env/launcher/IIS hashes matched the server backup. Tenant-scoped job inspection showed no active runtime/outreach work (Kasnak had 132 sent, 15 resolved, 1 handoff). Initial unscoped RLS query was empty and was NOT used as evidence; repeated with app.current_tenant before cutover.
- Stopped only AshiraaiAgentRecovery, AshiraaiAgentWorker, AshiraaiApi and AshiraaiReviewWeb. Verified tasks Ready, ports 8001/3101 closed and no remaining embedded Python app process. Vast tunnel and unrelated IIS sites untouched.
- Installed 382 candidate manifest files, preserving runtime data/.env. Web path `C:/sites/ashiraai/releases/progressive-20260914-r1/web`, node_modules junction to verified existing Windows dependency directory. Alembic upgraded to f56ef78ab90c, canonical channel binding applied, bootstrap created approved LIVE v15 and archived v14. Fingerprint `ef8b63e02b36b21f9777ccce2403fe8158a99bd8c52f6ab6b2b7c10eb231e93e`.
- Updated only named ReviewHttps/ReverseProxyToSelectionReview IIS rule matches to include locale roots/pages and api/platform, retaining API/webhook/media fallback. run-review.cmd points at the new release and sets production API/WEB/REVIEW origins. Restarted the four app tasks; first 4-second port check was too early, subsequent HTTP/runtime evidence confirmed startup.
- Found stale preflight expected migration f12. Updated to f56 and added migration-head regression test; 9 tests passed. CPU/WMIC initially yielded no valid values, later raw measurements and preflight passed; host parser unchanged. Updated production preflight by byte transfer with SHA-256 `26a7424a6bd745b4fb7a9100480705573f91a050762dd929b36a7546c56c9adc`.
- Full production preflight passed (`/tmp/progressive-production-preflight-v4.txt`): restricted DB/RLS role, current migration, approved LIVE, WABA/Meta read checks, Qwen, Redis, worker ping/tasks and CPU. Earlier failed/SSH-timeout attempts are not success evidence.
- Final r2 package `/tmp/sales-progressive-release-20260914-r2.zip`: SHA-256 `cbbf85b4aa519f345a3efbddbf65489ba3bf85bdf8d262e3adb0ce0990f9c9cb`, 382 files. Transferred to Windows staging; all deployed API/web bytes match r2 manifest (`/tmp/progressive-deployed-verification-v2.txt`). Release directory retains r1 name; contents match r2 (preflight correction only). Production .env hash remains unchanged.
- HTTPS /healthz and /tr return 200; development gallery 404; unauthenticated api/platform/auth/me 401. Real Chromium production login loaded without JS errors or mobile overflow; screenshot `/tmp/progressive-production-login.png` visually inspected.
- Created first platform invitation for requested info@verihane.net via canonical bootstrap; no existing user's role/password changed and no email sent. Private link stored only in server staging and provided separately to user, not committed to docs.
- GPU automation still PAUSED. No old staged package deployed; no old inbound/send replayed. Fresh user WhatsApp acceptance requested asynchronously. Production model acceptance process is separately tracked until completion; do not mark goal complete before required live evidence.
- Deployed Windows `verify_company_models.py` completed successfully: **25/25 actual Qwen checks** (`/tmp/progressive-deployed-model-acceptance.txt`). No DB writes or Meta send in this verifier. Fresh WhatsApp acceptance remains pending user input.

## Twenty-ninth turn — fresh WhatsApp acceptance check

- Previous turn was progress: deployment and production acceptance evidence.
- Read production DB with explicit Kasnak tenant context after deployment timestamp 2026-09-14T14:10:39Z. No new runtime jobs exist (`/tmp/progressive-fresh-whatsapp-check-1.txt`, post_deployment_jobs=[]).
- Therefore no fresh WhatsApp/model/Meta delivery evidence exists yet. User request remains pending; do not replay old messages or simulate acceptance. This is the first post-deployment blocked-input observation, not grounds to mark the goal complete or blocked yet.

## Thirtieth turn — second pending-input observation

- Prior turn made no implementation progress; it established the missing fresh-input gate.
- Revalidated production tenant-scoped runtime records: still no post-deployment jobs (`/tmp/progressive-fresh-whatsapp-check-2.txt`). This is the second consecutive observation of the same missing user-originated WhatsApp message. No replay, synthetic send, deployment or success claim made. Goal remains active pending the third-turn blocked audit threshold or actual new input.

## Thirty-first turn — blocked audit satisfied

- Previous turn made no implementation progress; the same required fresh user-originated WhatsApp input was absent.
- Third consecutive production check confirms no post-deployment runtime jobs (`/tmp/progressive-fresh-whatsapp-check-3.txt`). No meaningful further work can prove this live delivery gate without new inbound input. Goal marked blocked, not complete. Resume after the user sends a fresh WhatsApp message; inspect its actual version/model/job and Meta delivery without replaying old sends.

## Fresh user WhatsApp acceptance — 2026-09-14

- User confirmed sending a new message. Tenant-scoped production query found the new job created 2026-09-14 14:21:15.815945 UTC (17:21 Istanbul).
- Actual job: LIVE version 15, provider chat_compatible, model qwen3.8-27b, used_fallback=false, action=reply, fact_ids=[all_product_groups], attempts=1, outbound present, status=sent.
- Meta delivery audit for that same new job is **read**. This proves delivery and read receipt for the fresh message; no old job was replayed. Evidence `/tmp/progressive-fresh-delivery.txt`.
- Fresh-input blocker resolved. Do not infer that every unrelated workflow has a live Meta test from this single inbound acceptance.
