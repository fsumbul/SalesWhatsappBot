# Runtime genel değerlendirmesi — 16 Eylül 2026

Bu çalışma mevcut pytest/pytest-asyncio, pytest-cov/coverage.py, Ruff, mypy,
Next.js kontrolleri ve repo golden setlerini kullanır. Yeni bir test framework'ü
kurulmadı. Mevcut model kabul runner'ına tekrar, JSON checkpoint ve aşama süresi
raporlaması eklendi. GitHub CI kullanıcı tercihiyle kapatıldı; CI için hazırlanan
yerel commit kaldırıldı. Kontroller cihazda `make check-local` ile çalıştırılır;
JUnit ve coverage XML/JSON raporları git dışında `test-results/local/` altında
saklanır. Testler ve değerlendirme raporları repoya gönderildi.

## Sonuç

**Regresyon ve servis kontrolleri temiz; modelin ilgili bilgiyi kullanması ve yanıt
gecikmesi iyileştirme istiyor.** Otomatik kod testlerinin geçmesi, gerçek model
sorularının tamamının doğru cevaplandığı anlamına gelmiyor.

| Kontrol | Sonuç |
| --- | --- |
| Otomatik API kontrolleri | **687 geçti, 0 hata, 0 skip** |
| Ruff / mypy | Temiz / 163 kaynak dosya temiz |
| Web tip kontrolü / lint / build | Geçti; bir mevcut React uyarısı |
| Satır / karar dalı kapsamı | **%75,3 / %52,9** |
| Gerçek model kabulü | **39/48 (%81,25)**; üç soru tipinde toplam dokuz başarısızlık |
| Arama ilk üçte isabet | **10/12 (%83,3)**; hedef 12/12 |
| Arama MRR | **0,75**; repo hedefi ≥0,96 |
| Model kullanan yanıt hazırlama | **p50 23,9 sn; p95 47,2 sn; ortalama 25,4 sn** (42 örnek) |
| Deterministik menü hazırlama | p50 0,4 ms; p95 0,6 ms (6 örnek; gönderim hariç) |
| Uygulanabilir metin üretimi | **27 denemenin 13'ü timeout**, 14'ü başarılı |

48 ana değerlendirme ve iki düzeltilmiş senaryonun altı ek değerlendirmesi
olmak üzere toplam **54 puanlanan model denemesi**, ayrıca iki ısınma çağrısı
yapıldı. Tablodaki 48 örnek, hatalı tanımlanmış altı ilk sonucun yerine ayrı
koşulan altı düzeltme sonucunu kullanır. İlk ham rapor **33/48** olarak aynen
saklandı; başarısız sonuçlar gizlenmedi.

Bunlar sentetik/onaylı küçük bir senaryo kümesinin sonuçlarıdır; genel müşteri
trafiğinde %81 doğruluk iddiası veya p95 SLA garantisi değildir. Gecikmeler
Windows→model tünelini, bot kuyruğunu ve WhatsApp teslimini içermez.

## Öncelikli bulgular

1. **Planlama / konu eşleme:** `TS180118 kaç halat için?`, `Özel ölçü üretim
   yapıyor musunuz?` ve `kasnaklarınız sessiz mi çalışıyo` üç tekrarda da beklenen
   bilgiyi kullanamadı. İlk iki soru açıklama istemeye, üçüncüsü handoff'a düştü.
   TS180118 kaydı doğrudan aramada ilk sırada, sessiz çalışma bilgisi ikinci
   sırada bulunuyor. Sorun yalnız embedding doğruluğu değil; planlama aşamasının
   `unknown`/yanlış ürün kapsamı seçmesi bilgi kullanımını engelliyor.
2. **Üretim timeout'u:** Canlıda da doğrulanan 12 saniyelik üretim sınırı,
   değerlendirmedeki 27 uygun çağrının 13'ünde aşıldı. Sistem onaylı literal
   metne döndü. Genel `used_fallback=false` tek başına üretimin başarılı olduğunu
   göstermiyor; `generation.status` ayrıca izlenmeli. Timeout'u artırmak
   tek başına daha hızlı yanıt sağlamaz; önce planner, kanıt kararı ve üretim
   çağrılarının birlikte maliyeti üzerinden bir A/B çalışması yapılmalı.
3. **Arama sıralaması:** Captormal standart çapları ve özel ölçü üretimi ilk
   üçte bulunamadı. Bu, runtime'ın daha geniş aday havuzunu kullandığında mutlaka
   başarısız olacağı anlamına gelmez: Captormal boyut sorusu model testinde
   geçti. Reranker kapalı mevcut konfigürasyonun ilk-üç hedefi karşılanmıyor.
4. **Kapsam boşlukları:** Ek testler worker satır kapsamını yaklaşık %65'ten
   **%70,2**'ye, dal kapsamını %44,8'den **%53,0**'a çıkardı. Seçim akışı ve
   worker'ın kalan dalları ile gerçek tarayıcı iş akışları sonraki test önceliği.

Görünür fact ID sınırı bütün 48 örnekte korundu. Fiyat/stok senaryosu üç tekrarda
onaylı “bilinmiyor/yönlendirme” sınırını korudu; menüler altı kez LLM çağırmadan
çalıştı. Bunlar tam kapsamlı güvenlik veya tüm üretilen metinlerin bağımsız
anlamsal doğrulaması değildir; entailment reranker'ı bu ortamda kapalı.

## Kanıt dosyaları

[Makine okunur genel özet](reports/runtime-assessment-20260916/summary.json),
[ana model denemeleri](reports/runtime-assessment-20260916/model-acceptance.json),
[düzeltilmiş senaryolar](reports/runtime-assessment-20260916/model-corrected.json),
[arama sonuçları](reports/runtime-assessment-20260916/retrieval-detailed.json),
[kapsam özeti](reports/runtime-assessment-20260916/coverage-summary.json),
[681 testin JUnit raporu](reports/runtime-assessment-20260916/junit-final.xml),
[ek altı testin JUnit raporu](reports/runtime-assessment-20260916/junit-additional.xml),
[test ortamı sürümleri](reports/runtime-assessment-20260916/environment.json).

## Kapsam ve ölçüm yöntemi

- Kaynak tabanı: `b43dc51`; değerlendirme sırasında timing dekoratörünün tip
  sözleşmesi düzeltildi. Yanıt seçimi, model veya üretim ayarları değiştirilmedi.
- Tam API regresyonu gerçek ve yalnız test için kullanılan PostgreSQL üzerinde,
  sınırlı uygulama rolüyle çalıştırıldı; tenant izolasyonu RLS ile sınandı.
  FalkorDB/BGE-M3 servis entegrasyonları da çalıştı. `REQUIRE_DB_TESTS=1` ile
  atlanan testin sessizce başarılı sayılması engellendi.
- Coverage hem çalıştırılan satırları hem karar dallarını ölçer; bunlar farklı
  metriklerdir. [coverage.py branch coverage](https://coverage.readthedocs.io/en/7.14.0/branch.html).
- JUnit XML sonuçları ve `--durations` çıktısı tekrar üretilebilir test kanıtıdır.
  [pytest raporlama](https://docs.pytest.org/en/stable/how-to/output.html),
  [pytest süre ölçümü](https://docs.pytest.org/en/stable/how-to/usage.html).
- Bilgi arama: mevcut 12 soruluk `knowledge_golden.arti_kasnak.json`, BGE-M3,
  1024 boyut, reranker kapalı. Repo ADR hedefi ilk üçte isabet 1.00 ve MRR ≥0.96.
  Script'in `recall_at_k` değeri, birden fazla kabul edilen fact varsa bunlardan
  **en az birinin** ilk k içinde bulunma oranıdır; tam küme recall'u değildir.
- Gerçek model: mevcut Qwen3.8-27B, 16 senaryo × 3 tekrar; sabit bir ısınma çağrısı
  yüzdeliklerden çıkarıldı, soru sırası tekrarlar arasında döndürüldü. İki test
  tanımı düzeltildikten sonra bu iki senaryo ayrıca üçer kez çalıştırıldı.
- Model değerlendirmesi onaylı repo konfigürasyonunu kullanır: 63 fact. Canlıda
  v17/65 fact vardır. Hibrit mod, yalnız `details` üretimi ve 12 saniyelik üretim
  timeout'u canlı ayarlarla karşılaştırıldı. Testte müşteri hafızası ve yüklenmiş
  belge kanıtı yoktur; üretimdeki tüm müşteri bağlamını temsil etmez.
- Model/embedding çağrıları mevcut paylaşılan model sunucusundan yapılır.
  Başlangıçtaki örneklerin bir kısmı regresyon/indeks testleriyle aynı zaman
  aralığındadır; bu izole kapasite testi değildir. Coverage ayrı Python process'inde
  çalıştırıldı. Eşzamanlı yüksek yük, soak testi ve kapasite/SLA sonucu çıkarılmaz.
- Testler Meta'ya müşteri mesajı göndermedi. Model hazırlama süresi, worker kuyruğu,
  Windows SSH tüneli, Meta HTTP gönderimi veya telefon teslimi süresi değildir.

## Otomatik regresyon ve statik kontroller

- Tam mevcut paket: **681/681 başarılı**, sıfır skip; düzeltme sonrası tekrar da
  başarılı. Eklenen altı hata/erken çıkış testiyle **687 farklı kontrol başarılı**.
- Yeni vakalar: boş mesaj, opt-out, insan incelemesi nedeniyle erken çıkış;
  ilk başarısız denemenin yeniden denenebilir olması; beşinci denemede insan
  incelemesi; timing kapalıyken ek DB yazımı yapılmaması.
- Var olan belirsiz Meta yanıtı, çift webhook, sıralama/coalescing, STOP yarışı,
  tenant/rol izolasyonu ve kaynak/medya doğrulama testleri korundu.
- Ruff temiz; mypy **163 kaynak dosyada temiz**. İlk mypy koşusu timing
  dekoratörünün `Awaitable`/`Coroutine` tip uyumsuzluğunu yakaladı; tip sözleşmesi
  düzeltildi. Test ortamına repoda zaten tanımlı Celery stub'ları da kuruldu.
- Web TypeScript, lint ve üretim build'i başarılı. Seçim formundaki mevcut
  `useEffect`/`fetchView` dependency uyarısı devam ediyor.
- NIM OCR/vision/guardrail testleri sözleşme ve sahte servis testleridir;
  gerçek NIM modelleri prod'da kapalı olduğundan donanımlı NIM kabulü sayılmaz.

## Referans testi düzeltmeleri

1. “Kaç ülkeye satıyorsunuz?” sorusunun cevabı hem `company_history_and_reach`
   hem `company_overview` içinde onaylı olarak bulunuyor. İkinci kaydı seçen
   yanıtı hata saymamak için mevcut golden kabul listesine bu eşdeğer kayıt eklendi.
2. “Ölçüleri neler?” sorusunun `deflection_pulley` bağlamı, bu alt üründe doğrudan
   fact olmadığından ilk runner sürümünde boşalmıştı. Artık golden bağlamı önceki
   müşteri mesajında ürünün onaylı adıyla korunuyor. İlk bağlamsız sonuçlar ürün
   hatası olarak değerlendirilmez; düzeltilmiş testin sonuçları ayrıca saklanır.

İlk ham sonuçlar saklanır; bu iki senaryonun yeni koşuları karşılaştırılabilir
ayrı rapordadır. Diğer başarısız senaryoların beklenen sonucu değiştirilmedi.

## Canlı ortam kontrolü ve sınırlar

Üretim runtime/knowledge preflight, imzalı mesaj oluşturmayan webhook probu,
local/public health ve web kontrolleri geçti. Worker'lar çalışıyor; model ve
özel `.env` değişmedi. Son 50 işte yeni timing şemasına sahip müşteri örneği
yoktu; bu yüzden yeni ölçümlerle gerçek WhatsApp teslim dağılımı henüz hesaplanamaz.

Web'de otomatik oturum açma→işlem tamamlama tarayıcı testi tanımlı değil.
Web build'inin başarılı olması, tüm tarayıcı akışlarının kabulü değildir.
Kod coverage'ı ve kontrollü model vakaları da bütün olası kullanıcı mesajları
ve arızalar için garanti vermez.

## Tekrar çalıştırma

Python 3.12, Poetry ve pnpm ile bağımlılıklar kurulduktan, ayrı test DB'sine
migration uygulandıktan ve test ortam değişkenleri yüklendikten sonra repo kökünde:

```bash
make check-local
```

Bu komut API testleri, satır/dal coverage, Ruff, mypy, web lint, TypeScript ve
web build kontrollerini sırayla çalıştırır; ilk başarısız adımda durur.
`REQUIRE_DB_TESTS=1` nedeniyle atlanan testler başarı sayılmaz. Test DB bağlantısı
`LEADPULSE_TEST_DATABASE_URL` ile sınırlı uygulama rolüne ayarlanmalıdır; varsayılan
yerel `localhost:5433/leadpulse_test` bağlantısıdır. Üretim `.env` dosyasını kullanmayın.
GitHub'daki mevcut CI workflow'u `disabled_manually` durumundadır; workflow dosyası
repoda korunur fakat otomatik çalışmaz.

Model kabulü ve arama değerlendirmesini ayrıca çalıştırmak için mevcut komutlar:

```bash
cd apps/api
REQUIRE_DB_TESTS=1 pytest --cov=src --cov-branch --cov-report=json --cov-report=xml --junitxml=junit.xml --durations=20
ruff check .
mypy src
python scripts/verify_company_models.py --benchmark --hybrid --repeat 3 --report company-model-report.json
python ../../experiments/nim/retrieval_ab.py --ollama-url http://127.0.0.1:11439 --local-reranker '' --k 3 --report retrieval-report.json
```

Model komutu için `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`,
`KNOWLEDGE_BACKEND`, `EMBEDDING_PROVIDER`, `EMBEDDING_BASE_URL` ve ilgili test
servis ayarları gerekir. Üretim `.env` dosyasını test ortamına kopyalamayın.
