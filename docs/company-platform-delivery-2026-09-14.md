# Çok şirketli platform — 14 Eylül 2026 teslim kaydı

## Durum

Kod yerel çalışma ağacında uygulanmış, API + web paketi Windows staging alanına aktarılmıştır. **Üretim uygulama kodu, DB şeması, aktif Kasnak sürümü ve üretim `.env` henüz değiştirilmedi. Canlı E2E tamamlanmadı.**

Engel: mevcut Vast instance `50360379` (RTX A5000, 5.2.174.20) `exited/stopped`; start isteği `resources_unavailable` dönüyor. Kullanıcı **yeni makine kiralanmasını reddetti; mevcut makine beklenecek**. Başka modele, başka makineye veya ücretli kaynağa otomatik geçilmemeli. Model portları 11434, 11435 ve 11438 kapalı. Gerçek Kasnak model denemeleri `LLMCompletionError` ile fallback döndü; bunlar başarı değildir.

Yeni sohbet kapsamı boyunca takip duraklatıldı. Güncel paketin hash/import doğrulamasından sonra beş dakikalık aynı-task takibi yeniden ACTIVE yapıldı: otomasyon `kasnak-gpu-ve-e2e-yay-n`. Değişiklik olmayan durumda bildirim göndermez; kaynak açılınca yukarıdaki engeli aşarak teslim sırasını tamamlar. Yeni kiralama yasağı otomasyon talimatında da kayıtlıdır.

İlk platform yöneticisi e-postası kullanıcı tarafından **info@verihane.net** olarak belirlendi. Davet altyapısı hazırdır; üretim hesabı/daveti henüz oluşturulmadı. Mevcut Kasnak hesabının sales_manager rolü korunacak.

## Uygulanan işler

- Şirket kodu + e-posta + şifre ile oturum, sunucuda HttpOnly/Secure/SameSite çerezler; tek Next sürecinde eşzamanlı refresh tekilleştirme; CSRF origin kontrolü.
- Her istekte aktif kullanıcı/şirket ve güncel rol kontrolü; tenant kullanıcısının super_admin vermesi engellenir; son aktif sahip korunur. Platform şirket/davet işlemleri ayrı endpoint ve audit kayıtları kullanır.
- Kanonik `CompanyAgentConfig` üzerinde form, sohbet ve JSON/CSV önerileri; DB'de kalıcı önizleme; açık kabul/reddetme; taslak sürümü + revizyon kontrolü. CSV satır hatalarında kısmi uygulama yoktur.
- Kalıcı web test oturumları: seçili sürüm ve revizyon sunucudadır. Ortak CompanyAgentRuntime; seçilen onaylı bilgi kimliklerinden metin üretimi. Model/menü/fallback kaynağı, hata sınıfı, süre, sürüm ve insan devri görünürdür. Testler Meta'ya gönderim yapmaz.
- WhatsApp SenderProfile → Agent bağlantısı DB'dedir. Global slug seçimi kaldırıldı; canlı sürüm kanalın ajanından çözülür. Kurulmamış şirket, Kasnak kimlik bilgilerine düşemez.
- Yeni sürümde eski sürüm bot geçmişi modelden kesilir; arşiv korunur. Eski menü güncel `menu_fact_id` üzerinden gösterilir. Opt-out ve insan devri otomatik kaldırılmaz; gelen kutusunda açık devam işlemi vardır.
- Mevcut üretimden gelen teknik seçim, medya/kart menüleri ve özel operasyon sohbeti korunarak birleştirildi. Üretimdeki pilot konuşma filtresi kaldırıldı: yayınlanmış selection_flow şirketin tüm müşterileri için geçerlidir. Değişen seçim tanımı eski taslağı arşivler; eski cevaplar yeni sorulara uygulanmaz.
- Operasyon sohbetinde doğal metin gerçek modelle yorumlanır; yalnız `action:` menü eylemleri deterministiktir. Kullanıcıya özel DB/RLS geçmişi, idempotent işlem kimlikleri, dosya ve talep incelemesi korunur.
- Genel ham model test endpoint'i üretimde kapalıdır; eski frontend simülatör cevap yolu 410 döner. Eski review/admin-chat sayfaları ortak çalışma alanına yönlenir.

Kasnak ticari/teknik iddiaları değiştirilmedi. Depoda eksik olan, canlıda zaten kullanılan v14 kaynakları birleştirildi. Eklenen şirket konfigürasyonu alanı `agent.menu_fact_id=all_product_groups` eski menülerin güvenli yönlenmesini sağlar.


## Kullanıcının ek istediği sohbet operasyonları

- Doğal dil için gerçek LLM planlayıcıya `outreach`, `send_outreach`, `cancel_outreach`, `outreach_status`, `capacity`, `templates` araçları bağlandı. Şirket/kimlik/izin modeli seçemez; alıcılar kullanıcının metninden birebir kopyalanmalı. Menü düğmeleri kontrollü deterministic araç çağrılarıdır.
- Tek veya en fazla 100 E.164 numara; biçim doğrulama ve tekrarları kaldırma. LLM ikinci şema kontrollü çağrıda gerçekten Meta'dan gelen onaylı MARKETING şablonunu seçer. Serbest model metni Meta'ya gönderilmez. Destek: metin gövdesi, sabit metin başlık/altlık, konumsal gövde alanları ve QUICK_REPLY düğmeleri. Medya, Flow, dinamik URL ve named parameter şablonları bu ilk gönderim kartında desteklenmez; Kasnak mevcut müşteri Flow runtime'ı korunur.
- Kalıcı gönderim önizlemesi sohbetin içinde: metin, şablon alanları, alıcı bazında izin/engel bilgisi. Eksik tanıtım izni kullanıcı tarafından kaynağı ve tarihiyle karttan kaydedilebilir; LLM izin yaratamaz. Opt-out ve şirketin engelli listesi bu kayıtla aşılamaz. İzin bu gönderim için saklanır, global müşteri rızası otomatik değiştirilmez.
- Gönderim eylemi batch satır kilidiyle idempotent; kapasite rezervasyonları sender kilidiyle seri. Gönderilecek/engellenen alıcılar ayrı görünür. İşlem sonunda tahmini başarı verilmez: queued → sending → accepted → sent/delivered/read; belirsiz sonuçlar ambiguous ve tekrar yasak. Webhook makbuzları sıralama gerilemesine karşı korunur; erken makbuzlar opaque callback kimliğiyle eşleşebilir.
- Worker her gönderimden önce aktif kullanıcı/rol, tenant/kanal ve Meta onaylı şablonun değişmediğini yeniden doğrular. Telefon kilidi webhook opt-out işlemiyle aynıdır; `sending` durumu ağ POST'undan ÖNCE ayrı transaction'da commit edilir. Tek POST, taşıma retry yok. Mevcut runtime recovery görevi ve eski Celery beat giriş noktası yeni kuyruğu drene eder; ilave çalışan/schedule kurulumu gerekmez.
- Eski `/outreach/enqueue` artık 409 ile sohbet outbox yoluna yönlendirir. Eski outreach_jobs arşivi/makbuzları korunur ve worker tarafından tekrar oynatılmaz; eski global kimlik bilgisi fallback/retry yolu devreden çıktı.
- Meta limiti canlı WABA `whatsapp_business_manager_messaging_limit` alanından okunur; API erişimi yoksa "Alınamadı" görünür. Son 24 saatteki yerel gönderim/rezervasyon ve yerel kalan hak ayrı gösterilir. **Meta'nın portföyde kalan kesin kapasitesi API'den alınmıyor, yerel çıkarma sonucu Meta kapasitesi diye sunulmuyor.** Son kapasite kontrolünün zamanı gösterilir; platform dışında kullanılan portföy kotasını Meta uygulayacaktır.
- 14 Eylül 2026 yaklaşık 12:15 TRT salt okunur canlı kontrol: Meta `TIER_2K` (2.000) ve `ilk_bilgilendirme` MARKETING/APPROVED. Şablon: "Merhaba {{1}}, Artı Kasnak ürünleri hakkında bilgi almak ister misiniz?"; QUICK_REPLY "Bilgi Al" / "İlgilenmiyorum". Kimlik bilgileri çıktıya veya pakete alınmadı; gerçek gönderim yapılmadı.
- Teklif kartları mevcut müşteri-onaylı teknik talepleri gösterir. Sistem fiyatlandırılmış teklif veritabanına sahip olmadığından fiyatlı teklif veya tutar uydurulmaz. Kaynak/eksikler/dosya/konuşma/durum eylemleri sohbet içinde kalır.
- UI kullanıcının son isteğiyle tekrar sade sohbet olarak düzenlendi; ana ekranın orta noktasında tek mesaj kutusu, mesajlar başladıktan sonra altta sabit yazma alanı vardır. Mesaj geçmişi tek bir kaydırma alanıdır; kartların içinde ikinci bir sohbet kaydırması yoktur.
- 14 yeni PostgreSQL/RLS testi: atomik/idempotent gönderim, eşzamanlı worker ve kapasite rezervasyonu, gerçek rol/tenant reddi, consent/opt-out/blacklist, Meta kesintisi/onay iptali, belirsiz ve yarım kalmış gönderimin tekrar edilmemesi, teslimat sıralaması. Toplam **418 passed / 0 skipped**.
- Gerçek Chromium mevcut **16** platform akışı geçti. Ek `apps/web/e2e/operations.py` **10** arayüz/kart davranışını test eder: gerçek giriş, sağlayıcı/model yanıtları UI fixture; bu 10 test canlı Meta/model E2E başarısı sayılmaz. Ekranlar `/tmp/sales-operations-desktop.png`, `/tmp/sales-operations-mobile.png`.
- `verify_company_models.py` artık iki sektör/Kasnak müşteri runtime kontrollerine ek olarak gerçek modelle limit, teklifler, numara listesi, gönder ve gönderim durumu niyetlerini de doğrular; araçları çalıştırmaz, Meta'ya mesaj atmaz. GPU gelince bu ek kontroller de geçmeli.

Meta alan ve şablon davranışı için kullanılan birincil kaynaklar: [Meta Business SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/whatsappbusinessaccount.py), [Meta şablon API örnekleri](https://www.postman.com/meta/whatsapp-business-platform/request/f7o759z/fetch-message-templates), [WhatsApp Business Policy](https://whatsappbusiness.com/policy/).

## Doğrulama

- Tam backend suite gerçek PostgreSQL 16 + Redis 7 ile çalıştırıldı. **418 test geçti, 0 skip**; çıktı `/tmp/sales-all-tests.txt`. `REQUIRE_DB_TESTS=1` kullanıldığında herhangi bir skip tüm çalışmayı başarısız yapar.
- Ruff ve strict Mypy: 106 kaynak dosyası başarılı.
- Next.js üretim build + TypeScript + build lint başarılı.
- Gerçek Chromium: platform girişi, şirket oluşturma, sahip daveti/kabulü, tenant girişi, ajan/form/önizleme kabulü, yenileme sonrası kalıcılık, HttpOnly/Secure, rol menüleri, mobil taşma, 6 eşzamanlı refresh isteği, CSRF reddi, operasyon menüsü, çıkış doğrulandı.
- Son pakette Önceki API paketinin Windows staging hash/route importu ve üretim `.env` hash karşılaştırması başarılı. Son arayüz paketi 337 dosya içerir. Staging testinde boş `api/public/media` mount dizini oluşturuldu; üretim medya dosyaları pakete alınmadı.
- Windows staged Next.js `/tr` 200; oturumsuz `/api/platform/auth/me` 401. Geçici smoke sunucusu kapatıldı.
- Üretim olmayan HTTP testlerinde iki sektör/tenant, cross-tenant erişim reddi, aynı tenant'ta farklı kullanıcıların özel operasyon sohbeti, davet tekrar kullanımı, rol/suspension kontrolleri, revizyon çakışması, JSON/CSV ve model sınırları doğrulandı.
- Kanonik Kasnak config validate-only fingerprint: `ef8b63e02b36b21f9777ccce2403fe8158a99bd8c52f6ab6b2b7c10eb231e93e`.
- **Gerçek Qwen başarı kontrolü, üretim dağıtımı ve taze WhatsApp teslimatı bekliyor.** Arch 192.168.10.40 erişilemedi; test suite yerel gerçek DB/Redis üzerinde çalıştırıldı.

## Paket ve yedek

Repo: `/Users/binc/Documents/GitHub/SalesWhatsappBot`.

- API runtime: `C:\sites\ashiraai\app`.
- Staged release: `C:\sites\ashiraai\staging\company-platform-release-20260914`; `manifest.json` her dosyanın SHA-256 değerini, `archive-sha256.txt` paket hash'ini içerir. Yeni kod değişirse yeniden paketle/aktar.
- Yerel paket: `/tmp/sales-company-release-20260914.zip`, `scripts/package_company_release.py --output ...` ile oluşturulur. `.env`, anahtarlar, node_modules ve build cache pakete girmez. Üretimin mevcut public/media alanı korunur.
- Sunucu yedeği: `C:\sites\ashiraai\backups\multi-company-20260914-084723`.
- DB dump: `db-pre-agent-runtime-20260914-084724.dump`, 348642 byte. Üretim `.env` yalnız sunucu içindeki yedeğe kopyalandı.
- Yedekte `source-hashes.json` ve `env-sha256.txt` var. Env hash: `b74d7d5c522917c83dcc2348fe56a746bdb946e7cb740bfda88dda30292e28dd`. Dağıtım öncesi/sonrası karşılaştırılmalı.
- Mevcut DB head `e71ab2c903df`, Kasnak LIVE v14. Yeni head `f23bc45de67f`: `f12ab34cd56e` merge üzerine tenant RLS ile sohbet gönderim tabloları ekler. Yerel gerçek PostgreSQL üzerinde migration uygulandı.
- Mevcut frontend: `C:\sites\ashiraai\releases\admin-chat-simple-20260909-r2\web`, Next 15.0.3. Staged Windows smoke bu release'in node_modules junction'ını kullandı. Sohbet içi araçları içeren son paket 339 dosya. SHA-256: `63dc3b39519b1bae0b6daa78a3be86a9f76fbf1a9fd7df565fd441b3d60bdadc`.


## Son kullanıcı düzeltmesi: sade sohbet arayüzü

Kullanıcı E2E Platform başlığını ve yeni dashboard görünümünü kaldırıp daha önce beğendiği ChatGPT benzeri basit arayüzün geri gelmesini istedi. Canlıdaki `admin-chat-simple-20260909-r2` frontend kaynağı salt okunur incelendi; görsel düzen ve gezinme biçimi buna geri döndürüldü. **Eski dashboard paketini dağıtma.**

- Giriş sonrası varsayılan ekran sohbettir: “Bugün neye bakalım?”, tek mesaj kutusu ve üç küçük öneri.
- Kalıcı sol menü, şirket/test adı ve rol başlıkları, sürekli açık Meta kapasite paneli kaldırıldı. E2E Platform bir yerel test tenant etiketiydi, ürün adı değildi; ana arayüz artık bunu göstermez. Üretim şirketleri veya gerçek kullanıcı verileri silinmedi.
- Sohbet geçmişi açılır çekmece; yeni sohbet üstte küçük + düğmesi. Hesap menüsünden asistanlar, gelen kutusu, ekip ve yetkiye bağlı şirket yönetimi istenir; son kullanıcı talebiyle bunlar artık sohbet içinde kalıcı işlem kartları olarak açılır.
- Limit, teklifler ve gönderim kartları yalnız sohbetten istendiğinde açılır. HttpOnly auth, şirket izolasyonu, araçlar ve gönderim idempotency sınırı korunur.
- Enter gönderir; Shift+Enter yeni satır ekler. Mobil klavye/viewport yüksekliği izlenir. Gönderim sırasında çift tıklama ref kilidiyle engellenir; aynı istemci işlem kimliğiyle tekrar deneme korunur.
- Yeni Next üretim build/lint ve TypeScript geçti. Gerçek auth/şirket yapılandırması senaryosunun 16 kontrolü, yeni sade açılış + istekle limit + kart senaryosunun 10 kontrolü geçti. İkinci grupta Meta/model yanıtları fixture'dır; gerçek alıcıya gönderim yapılmadı.
- Masaüstü ve 390px mobil açılış ekranları: `/tmp/sales-simple-chat-desktop.png`, `/tmp/sales-simple-chat-mobile.png`. Ana ekranda E2E Platform metni, yatay taşma veya mobil boş dikey taşma bulunmadığı doğrulandı.
- Backend bu UI düzeltmesinde değişmedi; önceki 418 geçen DB/Redis testi geçerlidir. Canlı dağıtım hâlâ GPU/gerçek model doğrulamasını bekler.

## Son teslim kapsamı: bütün mevcut işlemler sohbet içinde

Kullanıcı “chatten çıkmadan her şeyi yaptırabiliyor olmamız lazım” istedi. Bu bölüm önceki ayrı yönetim ekranı davranışının yerini alır; eski staged UI paketlerini dağıtma.

- Ana ekran sade sohbet olarak kaldı. Hesap menüsü artık sayfa değiştirmez; sunucuda özel sohbet turu olarak kaydedilen işlem kartları açar. İzleyici de sohbetten kendi yetkisi kapsamındaki şirket/sürüm/gelen kutusu okumalarına erişebilir; hiçbir yazma yetkisi eklenmedi.
- Doğal mesajlar gerçek LLM planner üzerinden sınırlı `workspace` aracına yönlenir: şirket bilgisi, yapılandırma, müşteri testi, sürümler/yayın, asistan oluşturma, ekip/davet, şirket oluşturma/listeleme, gelen kutusu. Sabit UI düğmeleri `guided` kalır. Modelden SQL, endpoint, tenant veya işlem kimliği kabul edilmez; ad/kod/e-posta/mesaj metni kullanıcının mevcut mesajında bulunmalıdır. Davet rolü belirtilmemişse uydurulmaz; sohbet içinde tamamlanır.
- Bilgi ekleme doğrudan canonical builder + CompanyAgentConfig önizlemesi üretir. Kabul aynı sohbette yapılır. Test doğrudan CompanyAgentRuntime ile çalışır; sürüm, model, yanıt kaynağı, fallback ve süre kartta gösterilir. Aynı özel sohbetin test geçmişi korunur; seçilen asistan/revizyon değişince yeni test açılır. Doğal test varsayılanı mevcut taslak, taslak yoksa LIVE; farklı sürüm kartın müşteri testi seçicisinden seçilebilir.
- Yayınlama, şirket/asistan oluşturma ve tam belirtilen davetler önce somut kalıcı önizleme hazırlar. “Uygula” veya doğal açık onay ayrı işlem yapar. Yayınlama önizlemesi sürüm kimliği, revizyon ve önceki LIVE kimliğine bağlıdır; arada değişirse reddedilir. Eski/başka sohbete ait işlem tokenı çalışmaz. Tamamlanan önizleme geçmişte “Uygulandı/İptal edildi” görünür.
- DB işlemleri `TurnTransaction` ile canonical servislerin commitlerini flush'a çevirir; işlem, audit ve idempotent chat yanıtı tek dış transaction'da commit olur. Bu adapter **yalnız DB/config araçları içindir, Meta/manual gönderimde kullanılmaz**. Platform şirket oluşturma sonrası tenant ve private-user RLS bağlamı eski şirkete geri yüklenir. Aynı client_message_id aynı sonuç/davet linkini döndürür.
- JSON/CSV aktarımı, alan/sütun eşleştirme, rol değiştirme/etkinleştirme, başka şirket sahibini davet etme, sürüm geri alma ve manuel müşteri yanıtı/botu devam ettirme aynı sohbet içindeki canonical UI kartlarından tamamlanır. Bu kartlar ihtiyaç halinde daraltılır. Tam doldurulmuş doğal komutlar için desteklenen yazma araçları yukarıdadır; her yönetim alanına sınırsız doğal dil yazma yetkisi verildiği iddia edilmez.
- Tek numara/numara listesine WhatsApp tanıtımı, Meta limiti, gönderim durumu ve teklif talepleri mevcut sohbet araçlarını kullanmaya devam eder. “Teklif” burada müşteri tarafından onaylanmış teknik talep kaydıdır; fiyatlandırılmış teklif modülü yoktur.
- Doğrulama: **429 backend test geçti, 0 atlandı**, gerçek PostgreSQL/RLS ve Redis; Ruff ve strict mypy 107 kaynak dosyada temiz. Yeni 11 kontrol şirket+özel sohbet transaction'ı, davet/asistan idempotency, eski yayın revizyonu, tenant/rol/izleyici sınırları, context korunması, model hatasında rollback, gerçek runtime sınıfı üzerinden fixture model testi/fallback/Meta çağrılmaması ve negatif onayı kapsar.
- Next production build ve TypeScript geçti. Chromium'da mevcut 16 platform kontrolü + 10 operasyon kartı kontrolü + yeni 10 sohbet içi iş kontrolü geçti. Model/Meta fixture olan kontroller gerçek Qwen/WhatsApp teslimatı olarak sayılmaz. Görseller `/tmp/sales-chat-workspace-desktop.png`, `/tmp/sales-chat-workspace-mobile.png`.
- Mevcut Vast instance 50360379 son kontrolde `exited/stopped`; yeni kiralama yapılmadı. Gerçek model kabulü ve canlı dağıtım henüz tamamlanmadı. `verify_company_models.py` yeni 8 workspace niyetini de gerçek modelle doğrulamak üzere genişletildi.

## GPU hazır olunca tamamlanacak sıra

Artı Kasnak skill'ini ve production-operations referansını uygula. Yeni kiralama veya farklı GPU yok. Var olan instance'ın durumunu kontrol et; gerekiyorsa aynı instance için start isteğini yenile. Mevcut yerel Vast API anahtarı `/Users/binc/.config/vastai/arti-kasnak-test.key`; sadece sağlayıcı API'sine header içinde kullan, hiçbir çıktıya yazma. API davranışı: [instance yönetimi](https://docs.vast.ai/api-reference/instances/manage-instance).

1. Windows SSH: `Administrator@94.73.180.208`, key `/Users/binc/.ssh/sefertasi_vm_ed25519`. Mevcut model seçicisi `C:\sites\ashiraai\llm-runtime.cmd` **çağrılmalı**; `.env` tek başına eski Ollama ayarlarını gösterir. Seçici chat_compatible/qwen3.8-27b/localhost:11438 kullanır.
2. `AshiraaiVastTunnel` mevcut Vast makinesinde root@5.2.174.20:23619 → localhost:18080 bağlantısını localhost:11438'e taşır. Anahtar sunucuda `vast-tunnel/id_ed25519`; asla dışarı alma. Mevcut run.ps1/known_hosts korunur. Port değişirse aynı instance'ın doğrulanmış yeni bağlantısını kullan. Instance başlayınca model servisi kendiliğinden açılmamış olabilir: mevcut /workspace servis/başlatma dosyalarını ve model dosyalarını inceleyip aynı workload'ı başlat; model adı/komutu tahmin etme.
3. Staged API source ile `scripts/verify_company_models.py` çalıştır: iki farklı sektör aynı soruya kendi verileriyle cevap vermeli; Kasnak yazım hatası ve bilinmeyen fiyat/stok gerçek modelden geçmeli. Fallback veya hatalı seçim olursa düzeltmeden dağıtma. Script Meta göndermez. Gerçek builder çağrısı ve test geçmişi de doğrulanmalı.
4. Lokal kontrolleri gerekirse yeni değişiklikler için tamamla. Paket manifestini ve yedekten sonra canlı kaynak değişmediğini doğrula. Yedek eskidiyse yeni DB/dosya yedeği al. Üretim ortam dosyaları korunur.
5. Recovery, worker, API ve review web görevlerini durdur; ilgili port/process'lerin gerçekten durduğunu doğrula. Diğer IIS sitelerine/process'lere dokunma.
6. Paket `api/` dosyalarını uygulamaya kopyala. Embedded Python'da cwd import yoluna otomatik eklenmez: `sys.path.insert(0,'C:/sites/ashiraai/app')` kullan. Alembic upgrade head → bind_runtime_channel.py --tenant-slug kasnak --agent-slug arti-kasnak → bootstrap_arti_kasnak_agent.py. Yeni config yayınlanmalı; eski v14 arşivlenmeli. Başarılı DB/sürüm geçişinden sonra kör dosya rollback yapma; şema ve aktif config uyumluluğunu değerlendir.
7. Yeni web release'i `C:\sites\ashiraai\releases\multi-company-20260914\web` içine koy; node_modules için doğrulanmış Windows bağımlılıklarını kullan. `web/ops/run-workspace.cmd` → `C:\sites\ashiraai\run-review.cmd`. API_BASE_URL=http://127.0.0.1:8001, WEB_BASE_URL=https://api.ashiraai.com. Üretim `.env` değiştirilmez.
8. **IIS yönlendirmesi gerekir:** mevcut iis/web.config yalnız locale/review, locale/admin-chat, _next ve api/review yollarını 3101'e yönlendiriyor. Locale kökleri ve api/platform yollarını Next'e yönlendir; API v1, webhook ve medya yollarını 8001'de koru. Hedef web girişi `https://api.ashiraai.com/tr`. `ashiraai.com` başka IP'ye işaret ediyor; onu değiştirme. Yeni web yollarında HTTPS yönlendirmesini koru.
9. `bootstrap_platform_owner.py --company-code platform --email info@verihane.net --invite https://api.ashiraai.com` ile ilk platform davetini oluştur. Bu CLI tenant rol yükseltme API'sinden ayrıdır. Davet çıktısını kullanıcıya özel tut; eski Kasnak hesabının rolünü değiştirme. Davet linkini dağıtım başarıyla tamamlanınca kullanıcıya ver.
10. API/worker/recovery/review görevlerini başlat. `runtime_preflight.py --tenant-slug kasnak --require-llm` tüm DB/Meta/model/Redis/worker/task/host kapılarını geçmeli. Env hash ve dosya manifestini tekrar doğrula; HTTPS web girişini kontrol et. Model testi ve uçtan uca tenant akışını tekrar doğrula.
11. Kullanıcıdan **yeni bir WhatsApp test mesajı** iste; yeni runtime job/sürüm/model/Meta teslimatını kontrol et. Tamamlanmış veya belirsiz eski gönderimleri replay etme. Gerçek müşteri yanıtı olmadan WhatsApp teslimatı başarılı diye raporlama.

## Yerel tekrar çalıştırma

Python ortamı `/tmp/saleswhatsapp-venv/bin`; PostgreSQL container saleswhatsapp-e2e-db port55432, Redis saleswhatsapp-e2e-redis port56379. Colima default çalışıyor. Test DB `leadpulse_test`, restricted role `leadpulse_app`. Test URL/şifreler yalnız izole yerel test ortamına aittir.

```sh
# apps/api içinde; test ortam değişkenleriyle
REQUIRE_DB_TESTS=1 APP_DEBUG=false LEADPULSE_TEST_DATABASE_URL=postgresql+asyncpg://leadpulse_app:leadpulse_app_dev@127.0.0.1:55432/leadpulse_test REDIS_URL=redis://127.0.0.1:56379/9 CELERY_BROKER_URL=redis://127.0.0.1:56379/10 CELERY_RESULT_BACKEND=redis://127.0.0.1:56379/11 /tmp/saleswhatsapp-venv/bin/pytest -q
/tmp/saleswhatsapp-venv/bin/ruff check src scripts tests
/tmp/saleswhatsapp-venv/bin/mypy src
# apps/web içinde
node node_modules/next/dist/bin/next build
```

Chromium senaryosu `apps/web/e2e/workspace.py`; `E2E_WEB_URL` ve `E2E_PLATFORM_PASSWORD` kullanır. Varsayılan local web localhost:53000, şirket kodu e2e-platform, admin@example.com. Bu hesap yalnız yerel test DB'sinde vardır. Next API_BASE_URL=http://127.0.0.1:58000 ve WEB_BASE_URL=http://localhost:53000 ile başlatılır. API ayrı 58000 portunda çalışır. Şifreyi üretime taşıma.

## Açık ürün sınırları

Yeni şirket Meta/Embedded Signup kurulumu, PDF/Excel aktarımı, firma keşfi ve tam kampanya yönetimi ikinci aşamadadır. Kullanıcının ek talebiyle tek numaraya / en fazla 100 numaralık listeye sohbetten tanıtım gönderimi ilk teslim kapsamına alındı. Yeni tenant webte çalışır ve WhatsApp bağlantısı “Kurulmadı” görünür. Refresh tekilleştirmesi bu Windows dağıtımındaki tek Next süreci içindir; birden fazla Next replica'ya geçmeden ortak refresh koordinasyonu eklenmelidir.
