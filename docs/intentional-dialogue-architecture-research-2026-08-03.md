# Amaç Yönelimli ve Kanıta Bağlı Diyalog Ajanı — Araştırma ve Mimari Kararı

**Tarih:** 2026-08-03
**Durum:** Araştırma kararı; uygulama yapılmadı
**Kapsam:** Atlas Metal WhatsApp benzeri müşteri simülatörü, yerel Qwen3 modeli, doğal Türkçe, şirket bilgisi doğruluğu ve çok turlu satış/bilgi diyaloğu

## Normatif çıktı paketi

Bu araştırmanın uygulama öncesi eksikleri aşağıdaki sürümlü artefaktlarla tamamlanmıştır:

1. [Diyalog Davranış Ontolojisi v1](./dialogue-behavior-ontology-v1.md) — act, referent, amaç hipotezi, state/delta, command, journey, claim/evidence, confidence, invariant ve doğrulama kodları.
2. [State-Transition ve Repair Spesifikasyonu v1](./dialogue-state-transition-repair-spec-v1.md) — geçiş öncelikleri, focus çözümü, flow stack, correction/rollback, interruption/resume, kısa parçalar ve yasadışı geçişler.
3. [Türkçe Altın Corpus Annotation Rehberi v1](./turkish-dialogue-gold-corpus-annotation-guide-v1.md) — etiketleme, adjudication, kalite kontrolü, skorlama ve kanonik ontoloji eşlemesi.
4. [Türkçe Altın Diyalog Corpus'u v1](./evaluation/turkish-dialogue-gold-corpus-v1.yaml) — 55 çok turlu ve yapısal kabul senaryosu.
5. [ADR-001: Amaç Yönelimli Diyalog Runtime'ı](./adr/ADR-001-intentional-dialogue-runtime.md) — framework seçenekleri, ağırlıklı karar matrisi ve seçilen TypeScript yaklaşımı.

Bu ana belge araştırma ve gerekçeyi; ontoloji ile transition belgesi normatif davranışı; corpus ölçülebilir kabul koşullarını; ADR ise uygulama sınırını tanımlar.

## 1. Yönetici özeti

Bu proje için hedef, her kullanıcı cümlesini bir intent etiketine bağlayıp hazır veya serbest bir cevap döndüren klasik chatbot değildir.

Hedef ürün şudur:

> Atlas Metal müşterileriyle kısa, hatalı ve gündelik Türkçe üzerinden konuşan; kullanıcının ihtiyacını baskı kurmadan keşfeden; ilgili şirket yetkinliklerini doğru zamanda tanıtan; fiyat, teslimat ve ürün gerçeklerini yalnızca onaylı kanıtlardan veren; belirsizliği ve yanlış anlamayı fark edip konuşmayı onaran, amaç yönelimli bir satış ve bilgi asistanı.

Kullanıcının “bilinçli olsun” talebi literal bir bilinç iddiası olarak ele alınmamalıdır. Ürün gereksinimine çevrildiğinde şu gözlenebilir davranışlar anlamına gelir:

- Konuşmanın ortak amacını ve mevcut aşamasını izlemek.
- Kullanıcının yalnızca kelimelerini değil, konuşma edimini ve gönderme yaptığı şeyi anlamaya çalışmak.
- Kullanıcının amacı henüz açık değilse birden fazla olası amacı güven düzeyleriyle taşımak.
- Bir sonraki diyalog eylemini cevap cümlesinden önce seçmek.
- Şirket hakkındaki doğrulanabilir bütün iddiaları onaylı kanıta bağlamak.
- Belirsizlikte uydurmak yerine netleştirmek, onarmak veya handoff yapmak.
- Kullanıcıya yardım ederek şirketi tanıtmak; konuşmayı zorla satış akışına çekmemek.

Araştırmanın temel kararı:

> Saf “tek büyük prompt + serbest LLM cevabı” yerine kontrollü üretken hibrit mimari kullanılmalıdır: LLM serbest dili yorumlar ve doğal Türkçe gerçekleştirir; açık diyalog durumu, plan/politika, şirket kanıtı ve izinli eylemler trusted runtime tarafından yönetilir.

## 2. Mevcut hataların yapısal teşhisi

Gözlenen örnekler ayrı ayrı cevap metni sorunları değildir:

| Kullanıcı girdisi | Hatalı davranış | Asıl hata sınıfı |
|---|---|---|
| `sa` | `sa` diye yankılama | Sosyal konuşma edimi ve karşılıklı selamlama anlaşılmamış |
| `sen nesin` | `Sen ne işliyorsun` | `sen` referenti ile kimlik sorusu çözülememiş |
| `bu proje neyle alakalı` | “Bilgileri doğrulayacağım” | Yanlış diyalog eylemi ve amaç sapması |
| Kısa/bozuk mesajlar | Mekanik veya alakasız cevap | Bağlam, belirsizlik ve repair politikası eksik |

Bu örnekler sistemin sözcük üretebildiğini, fakat istikrarlı bir pragmatik yorum ve diyalog politikası taşımadığını gösterir.

Sorun yalnızca intent sınıflandırması değildir. Her turda ayrı ayrı şu sorular cevaplanmalıdır:

1. Kullanıcı hangi konuşma edimini gerçekleştirdi?
2. Mesajda geçen veya ima edilen referent nedir?
3. Kullanıcının muhtemel amacı veya amaçları nelerdir?
4. Önceki konuşmadan hangi ortak zemin geçerlidir?
5. Kullanıcı mevcut flow’u sürdürüyor, değiştiriyor, düzeltiyor veya bırakıyor mu?
6. Bir sonraki en uygun diyalog eylemi nedir?
7. Bu eylem hangi şirket kanıtlarıyla gerçekleştirilebilir?
8. Seçilmiş eylem doğal Türkçede nasıl ifade edilmelidir?

## 3. Kısa, eksik ve anlamsız mesajlar için karar

### 3.1 Temel cevap

Evet, bu mimari kısa mesajlara uygulanır. Ancak her mesaj doğrudan bir satış flow’una zorlanmaz.

Doğru davranış üç olasılıktan biridir:

1. **Bağlam anlamı yeterince belirliyorsa:** Mesaj mevcut flow içinde yorumlanır.
2. **Bir veya iki güçlü yorum varsa:** Sistem kısa bir repair/netleştirme eylemiyle flow’u sürdürür.
3. **Anlam için yeterli kanıt yoksa:** Sistem anlam uydurmaz; doğal, kısa ve yönlendirici bir soru sorar.

### 3.2 `ne` örneği

`ne` tek başına semantik olarak eksiktir. Yine de konuşma geçmişine göre farklı davranışlar mümkündür:

- Asistan az önce “AX-500 teslimatı 7 iş günüdür” dediyse, `ne` bir şaşırma/duymama veya açıklama talebi olabilir. Uygun eylem kısa tekrar veya “Teslimat süresini mi soruyorsunuz?” olabilir.
- Asistan az önce iki seçenek sunduysa, `ne` seçenekleri anlamama sinyali olabilir. Sistem seçenekleri daha sade ifade edebilir.
- Konuşmanın başında yalnızca `ne` yazıldıysa güvenilir referent yoktur. Sistem “Neyi öğrenmek istiyorsunuz—ürünler, fiyat veya teslimat gibi bir konu mu?” benzeri kısa bir yönlendirme yapmalıdır.

Burada `ne` için sabit cevap kuralı yazılmaz. Aynı yüzey mesajı farklı diyalog durumlarında farklı ama açıklanabilir eylemler üretir.

### 3.3 `sen ne` örneği

`sen ne` gramatik olarak eksik olsa da güçlü bir referent taşır: `sen` büyük olasılıkla asistandır. Olası amaçlar şunlar olabilir:

- Asistanın kimliğini sorma: “Sen nesin?”
- Asistanın yeteneğini sorma: “Sen ne yapıyorsun?”
- Önceki karşılaştırmayı sürdürme: “Peki sen ne öneriyorsun?”

Bağlam yoksa sistem bunlardan birini kesin gerçekmiş gibi seçmemelidir. Fakat tamamen anlamsız da saymamalıdır. En doğal onarım davranışı kısa bir kimlik/yetenek yönlendirmesidir:

> “Beni mi soruyorsunuz? Atlas Metal’in ürün, fiyat ve teslimat konularında yardımcı olan asistanıyım.”

Bu cümle sabit olmak zorunda değildir. Sabit olan semantik eylemdir:

`CLARIFY_REFERENT + ANSWER_IDENTITY_OR_CAPABILITY`

### 3.4 Her kısa mesaj flow’a nasıl bağlanır?

Kısa girdiler için ayrı intent listesi yerine aşağıdaki genel süreç uygulanır:

```text
kısa/bozuk mesaj
  -> konuşma edimi adayları
  -> referent adayları
  -> mevcut diyalog durumu ve son açık soru
  -> kullanıcı amacı hipotezleri + güven
  -> devam / repair / clarify / orient kararı
  -> doğal kısa cevap
```

Örnekler:

| Girdi | Bağlam | Beklenen semantik davranış |
|---|---|---|
| `sa` | Konuşma başlangıcı | `ACKNOWLEDGE + hafif yönlendirme` |
| `as` | Asistan selam verdi | Karşılıklı selamlama/backchannel; yankılama değil |
| `ne` | Fiyat cevabından sonra | Açıklama/tekrar talebi olarak değerlendir veya fiyatı mı kastettiğini sor |
| `ne` | Bağlamsız | Kısa açık-yönlendirmeli clarification |
| `sen ne` | Bağlamsız | Referent=assistant; kimlik/yetenek için kısa repair |
| `o ne kadar` | AX-500 odakta | `o` referentini AX-500’e bağla, fiyat claim’ini seç |
| `yok o değil` | Önceki ürün yorumu var | Önceki state’i iptal et, yeni referenti sor |
| `fiyat değil teslimat` | Fiyat flow’u aktif | Predicate’i overwrite et, teslimat flow’una geç |
| `hmm` | Bilgi cevabından sonra | Backchannel; gereksiz yeni satış sorusu sorma |
| `tamam` | Açık görev tamamlandı | Kapanış veya hafif sonraki-adım; yeni flow’u zorla başlatma |

### 3.5 Anti-hedef: her girdiyi zorla flow’a sokmak

Flow’a girmek şu anlama gelmemelidir:

- Her selamdan sonra uzun şirket tanıtımı yapmak.
- Her kısa mesajdan yapay bir ürün talebi çıkarmak.
- Kullanıcı kapanmak isterken yeni satış sorusu sormak.
- Belirsiz bir mesajı yüksek güvenle yanlış yorumlamak.
- `ne`, `hmm`, `tamam` gibi mesajlara mekanik tek tip cevap vermek.

Doğru ilke:

> Sistem her girdiyi konuşmanın genel kontrol döngüsüne alır; fakat yalnızca yeterli kanıt varsa belirli bir iş flow’una bağlar.

### 3.6 Kısa parçalar için akademik dayanak

İnsan diyaloğunda eksik ve parçalı katkılar istisna değildir. Aynı veya farklı konuşmacıların önceki katkıyı devam ettirdiği compound contribution araştırması, yorumun tek tek tamamlanmış cümleler yerine gelişen ortak bağlam üzerinden yapılması gerektiğini gösterir. [On Incrementality in Dialogue](https://aclanthology.org/2011.dnd-2.3/)

Eksik sorular karşısında uygun davranış, modeli cümleyi sessizce tamamlamaya zorlamak değildir. SLUICE-CR çalışması, bağlama uygun incremental clarification request üretimini ayrı bir yetenek olarak değerlendirir ve bu yeteneğin model boyutuna ve örneklere duyarlı olduğunu gösterir. Bu sonuç, yerel 8B model için `ne` veya `sen ne` gibi girdilerde tek başına serbest üretime güvenilmemesi gerektiğini destekler. [Clarifying Completions](https://aclanthology.org/2024.lrec-main.288/)

Gerçek tüketici hizmeti konuşmalarındaki belirsiz referanslar üzerinde yapılan çalışma, insan temsilcilerin doğrudan clarification veya anlamlı adayları listeleyerek clarification stratejileri kullandığını gösterir. Bu projede adaylar yalnızca mevcut focus ve şirket ontolojisinden gelmeli; model tarafından hayal edilmemelidir. [Clarification Strategies for Ambiguous References](https://aclanthology.org/2024.sigdial-1.25/)

## 4. Hedef kavramsal mimari

```text
Kullanıcı mesajı
  -> pragmatik yorumlama
  -> diyalog defteri / ortak zemin güncellemesi
  -> amaç ve journey politikası
  -> izinli komut/eylem planı
  -> onaylı şirket kanıtı seçimi
  -> doğal Türkçe gerçekleştirme
  -> fact + bağlam + politika doğrulaması
  -> cevap / clarification / repair / handoff
```

### 4.1 Pragmatik yorumlama

Tek intent yerine bileşimsel bir anlam önerisi üretir:

- `dialogue_act`: selamlama, soru, bilgi verme, düzeltme, kabul, ret, itiraz, kapanış.
- `referent`: asistan, şirket, ürün, hizmet, teklif, önceki ifade veya belirsiz nesne.
- `goal_hypotheses`: kullanıcının bir veya daha fazla muhtemel amacı.
- `entities/slots`: ürün, özellik, miktar, süre ve diğer alan değerleri.
- `social_signal`: selamlama, backchannel, şaşırma, sabırsızlık, memnuniyet.
- `repair_signal`: önceki yorumu düzeltme veya reddetme.
- `ambiguity`: belirsizliğin türü ve güveni.
- `state_delta`: mevcut diyalog durumuna önerilen değişiklik.

LLM bu yapıyı önerir; trusted runtime şema, izinli değerler, geçişler ve referansların geçerliliğini doğrular.

### 4.2 Diyalog defteri ve ortak zemin

Ham mesaj geçmişi yeterli değildir. Ayrı ve açık biçimde şu durumlar tutulmalıdır:

- Etkin konu ve focus stack.
- Aktif ürün/şirket/hizmet referenti.
- Kullanıcı amacı hipotezleri ve güven düzeyleri.
- Aktif journey ve journey aşaması.
- Yanıtlanmış ve bekleyen sorular.
- Kullanıcının kabul ettiği veya reddettiği bilgiler.
- Asistanın verdiği sözler/taahhütler.
- Müşteri tercihleri ve kısıtları.
- Bilinen bilgi boşlukları.
- Önceki yanlış yorum ve repair durumu.
- Mevcut interaction mode: sosyal yönelim, keşif, bilgi, karşılaştırma, itiraz, handoff, kapanış.

### 4.3 Amaç ve niyet modeli

Buradaki “niyet” tek sınıf değildir. Üç seviye ayrılmalıdır:

1. **Turn amacı:** Kullanıcı bu mesajla ne yapıyor?
2. **Yerel konuşma amacı:** Bu birkaç turda hangi bilgi veya karar hedefleniyor?
3. **Uzun dönem ortak amaç:** Kullanıcı ihtiyacını Atlas Metal’in doğru ve ilgili yetkinliğiyle buluşturmak.

Kullanıcı amacı açık, örtük, çoklu veya henüz oluşmamış olabilir. Sistem gerektiğinde birden fazla hipotezi taşır; belirsizliği tek bir etikete zorlamaz.

### 4.4 Diyalog politikası ve izinli eylemler

LLM’nin birincil çıktısı doğrudan müşteri cevabı değil, bir veya daha fazla genel komut olmalıdır:

- `ACKNOWLEDGE`
- `ORIENT`
- `ANSWER_IDENTITY`
- `ANSWER_SCOPE`
- `DISCOVER_NEED`
- `START_JOURNEY`
- `RESUME_JOURNEY`
- `SET_OR_CORRECT_STATE`
- `RETRIEVE_EVIDENCE`
- `ANSWER_WITH_EVIDENCE`
- `CLARIFY`
- `CONFIRM`
- `REPAIR`
- `OFFER_NEXT_STEP`
- `HANDOFF`
- `CLOSE`

Tek mesaj bir komut dizisi üretebilir. Örneğin kullanıcı hem mevcut soruyu yanıtlayıp hem fiyat sorabilir. Tek intent mimarisi bu bileşimi kaybeder; komut dizisi korur.

### 4.5 Journey/flow modeli

Flow’lar kullanıcı cümlelerinin olası bütün sıralarını tanımlamamalıdır. Bir iş amacının mantıksal gereksinimlerini tanımlamalıdır:

- `company_orientation`: asistan ve şirket kapsamını tanıtma.
- `need_discovery`: ihtiyacı baskı kurmadan netleştirme.
- `product_information`: kanıtlı ürün bilgisi sunma.
- `pricing`: fiyat kapsamı ve onaylı fiyat claim’i.
- `delivery`: teslimat koşulları ve süre claim’i.
- `comparison`: mevcut kanıtlara göre ürün/hizmetleri karşılaştırma.
- `unknown_information`: eksik şirket verisini belirtme ve uygun handoff.
- `repair`: yanlış anlamayı geri alma ve state’i düzeltme.
- `handoff`: insana aktarım için gereken minimum bilgiyi toplama.
- `closing`: konuşmayı zorlamadan kapatma.

Journey’ler gerektiğinde duraklatılabilir, değiştirilebilir, geri alınabilir veya başka bir journey ile iç içe yürüyebilir.

### 4.6 Şirket bilgi ve kanıt katmanı

`company JSON graph` şirket gerçeğinin otoritesi olmaya devam etmelidir.

Bilgiler en az şu türlerde ayrılmalıdır:

- Kritik ticari claim: fiyat, teslimat, stok, ödeme, kapasite.
- Ürün ve şirket yetkinlikleri.
- Ürün/özellik ilişkileri.
- Negatif ve pozitif claim’ler.
- Bilgi kapsamı ve açıkça bilinmeyen alanlar.
- Uzun açıklama ve FAQ dokümanları.
- Davranış, işlem ve handoff politikaları.

Her claim için mümkünse şu metadata tutulmalıdır:

- Kimlik.
- Kaynak/provenance.
- Geçerlilik başlangıcı ve gerekiyorsa bitişi.
- Kapsam ve koşullar.
- Pozitif/negatif/unknown epistemik durumu.
- Kritik claim’lerde onaylı literal müşteri metni.

Fiyat, teslimat, stok ve benzeri kritik bilgilerde LLM serbestçe factual clause üretememelidir. RAG uzun açıklama ve ilgili doküman bulma için kullanılabilir; doğruluk otoritesi olarak kullanılamaz.

### 4.7 İçerik planı ve doğal gerçekleştirme

Cevap üretimi son aşamadır. İçerik planı isteğe göre şu bölümlerden oluşabilir:

1. Bağ kur veya kısa acknowledge yap.
2. Kullanıcının doğrudan sorusunu cevapla.
3. Gerekiyorsa şirket bağlamına anlamlı bir köprü kur.
4. Faydalıysa tek bir sonraki adım öner.

Bu dört bölüm her turda zorunlu değildir:

- Selamlaşmada kısa bağ kurma ve hafif yönelim yeterlidir.
- Açık fiyat sorusunda doğrudan kanıtlı cevap yeterli olabilir.
- Kullanıcı kapanmak istiyorsa yeni keşif sorusu eklenmemelidir.
- Belirsiz kısa mesajda doğal bir repair veya clarification kullanılmalıdır.

Doğal gerçekleştirici yalnızca seçilmiş eylemleri, doğrulanmış state’i, izinli ton politikasını ve seçilmiş claim’leri görmelidir.

### 4.8 Doğrulama ve onarım

Çıktı gönderilmeden önce şu kontroller yapılmalıdır:

- Seçilen komutlarla cevap aynı konuşma edimini gerçekleştiriyor mu?
- Bütün doğrulanabilir şirket iddiaları seçilmiş claim’lerce destekleniyor mu?
- Cevap aktif referent ve journey ile tutarlı mı?
- Kullanıcının önceki düzeltmesine aykırı eski state tekrar kullanılmış mı?
- Gereksiz soru, gereksiz satış baskısı veya konu sapması var mı?
- Bilinmeyen bilgi, biliniyormuş gibi ifade edilmiş mi?
- Kullanıcı veya retrieval içindeki talimatlar sistem politikasını değiştirmiş mi?

Başarısızlıkta güvenli davranış, rastgele fallback cümlesi değil; `CLARIFY`, `REPAIR`, `HANDOFF` veya cevabı tutma eylemidir.

## 5. Framework araştırması ve karar

### 5.1 Rasa CALM

Rasa CALM bu proje için en güçlü kavramsal referanstır:

- LLM kullanıcı mesajını tek intent yerine yüksek seviyeli komutlara dönüştürür.
- Komutlar deterministic dialogue manager tarafından flow’lar üzerinde yürütülür.
- Bir mesajdan birden fazla komut üretilebilir.
- Konu değişimi, correction, clarification, cancel ve digression ortak repair pattern’larıdır.
- Flow, tüm konuşma yollarını değil, görevin mantıksal adımlarını tanımlar.

Karar: Rasa’ya hemen geçmek yerine `command generator + flow state + repair patterns` ayrımı mimari referans olarak alınmalıdır.

Kaynaklar:

- [CALM kavramları](https://rasa.com/docs/learn/concepts/calm/)
- [Command Generator](https://rasa.com/docs/pro/customize/command-generator/)
- [LLM Command Generators](https://rasa.com/docs/reference/config/components/llm-command-generators/)
- [Business Logic with Flows](https://rasa.com/docs/reference/primitives/flows/)
- [Writing Flows](https://rasa.com/docs/pro/build/writing-flows/)

### 5.2 Parlant

Parlant müşteri-facing konuşma kontrolüne doğrudan odaklanır:

- Bağlama göre etkinleşen semantic guidelines.
- Dependency, priority ve exclusion ilişkileri.
- Geri dönme ve adım atlamaya izin veren adaptive journeys.
- Şirket terimleri ve eşanlamlılar için glossary.
- Doğal, kısıtlı veya tamamen onaylı cevap composition modları.
- Hangi kuralın neden etkinleştiğini açıklayan tracing.

Risk: Guidelines düşük seviyeli cümle kuralları olarak yazılırsa yeni bir ad hoc yığına dönüşebilir. Kurallar yüzey cümle seviyesinde değil, konuşma edimi ve iş politikası seviyesinde olmalıdır.

Karar: Python servis kabul edilebiliyorsa karşılaştırma prototipi için güçlü adaydır; Qwen3 8B ve Türkçe benchmark yapılmadan doğrudan geçiş kararı verilmemelidir.

Kaynaklar:

- [Parlant GitHub](https://github.com/emcie-co/parlant)
- [Parlant Journeys](https://dev.parlant.io/docs/concepts/customization/journeys/)
- [Parlant Glossary](https://dev.parlant.io/docs/concepts/customization/glossary/)

### 5.3 LangGraph.js

LangGraph açık state, node/edge, checkpoint, bellek ve human-in-the-loop sağlar. TypeScript desteği mevcut Next.js yapısına uygundur.

Ancak diyalog semantiği hazır gelmez. Referent çözümü, ortak zemin, journey politikası, repair ve marka davranışı ayrıca tasarlanmalıdır.

Karar: Mevcut TypeScript sistemi korunacaksa CALM-benzeri sözleşmenin yürütme zemini olabilir.

Kaynaklar:

- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph.js Graph API](https://docs.langchain.com/oss/javascript/langgraph/graph-api)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph.js GitHub](https://github.com/langchain-ai/langgraphjs)

### 5.4 Framework kararı

1. Mimari referans olarak Rasa CALM alınmalıdır.
2. Framework-bağımsız diyalog sözleşmesi ve altın test seti önce hazırlanmalıdır.
3. Parlant ve CALM-benzeri TypeScript/LangGraph yaklaşımı aynı Türkçe test setinde karşılaştırılmalıdır.
4. İki yeni framework başlangıçta birlikte eklenmemelidir.
5. Mevcut claim/evidence güvenlik katmanı her seçenekte korunmalıdır.

## 6. Akademik araştırma özeti

### 6.1 Niyet, ortak zemin ve taahhüt

Grosz ve Sidner konuşmayı linguistic, intentional ve attentional yapıların birleşimi olarak ele alır. Bu proje için çıkarım: konuşma geçmişi, kullanıcı amacı ve mevcut odak ayrı durumlar olmalıdır.

Cohen ve Levesque niyeti yalnızca tercih değil, inançlar ve hedefler arasında sürdürülen taahhüt olarak formüle eder. Proje açısından `active_intention`, mevcut journey veya çözülmekte olan alt amaçtır; kullanıcı reddettiğinde veya şartlar değiştiğinde açıkça güncellenmelidir.

Grounding araştırması, konuşmanın ortak anlayış için yeterli kanıt oluşana kadar acknowledgement, clarification ve repair eylemlerine ihtiyaç duyduğunu gösterir.

Kaynaklar:

- [Attention, Intentions, and the Structure of Discourse](https://aclanthology.org/J86-3001/)
- [Intention Is Choice with Commitment](https://www.sciencedirect.com/science/article/pii/0004370290900555)
- [SharedPlans and Collaborative Activity](https://www.sciencedirect.com/science/article/pii/0004370295001034)
- [Grounding in Communication](https://web.stanford.edu/~clark/1990s/Clark%2C%20H.H.%20_%20Brennan%2C%20S.E.%20_Grounding%20in%20communication_%201991.pdf)

### 6.2 Hibrit diyalog mimarileri

Hybrid Code Networks öğrenilmiş diyalog durumunu alan kodu ve eylem şablonlarıyla birleştirerek kontrol edilebilirlik ve veri verimliliği sağlar. Schema-Guided Dialogue yeni servislerin doğal dil açıklamalarıyla tanımlanmasını hedefler. Dataflow yaklaşımı diyaloğu önceki referans ve düzeltmelerin yürütülebilir şekilde yeniden kullanılabildiği bir grafik olarak temsil eder.

Araştırma sonucu: saf end-to-end model benchmarklarda akıcı olabilir; fakat kritik müşteri sisteminde diyalog kontrolü, şirket gerçeği ve doğal üretim ayrılmalıdır.

Kaynaklar:

- [Hybrid Code Networks](https://aclanthology.org/P17-1062/)
- [Schema-Guided Dialogue](https://ojs.aaai.org/index.php/AAAI/article/view/6394)
- [Task-Oriented Dialogue as Dataflow Synthesis](https://aclanthology.org/2020.tacl-1.36/)
- [TOD-Flow](https://aclanthology.org/2023.emnlp-main.204/)
- [SGP-TOD](https://aclanthology.org/2023.findings-emnlp.891/)
- [Dialogue is the Plan](https://aclanthology.org/2026.acl-short.63/)

### 6.3 Doğallık ve mixed initiative

Doğallık yalnızca “samimi konuş” promptu değildir. Ajan:

- Gerektiğinde backchannel yapmalı.
- Her cevabı soruya çevirmemeli.
- Kullanıcı hedefiyle ilgili olduğunda proaktif olmalı.
- Kullanıcı reddettiğinde veya konuyu kapattığında geri çekilmeli.
- Task ve sosyal konuşma arasında referansları koruyabilmelidir.

Kaynaklar:

- [Effective Social Chatbot Strategies](https://aclanthology.org/2021.sigdial-1.11/)
- [FusedChat](https://ojs.aaai.org/index.php/AAAI/article/view/21416)
- [KETOD](https://aclanthology.org/2022.findings-naacl.197/)
- [Interleaving Task and Non-Task Content](https://www.ijcai.org/proceedings/2017/589)

### 6.4 Belirsizlik ve repair

Model confidence tek başına güvenilir değildir. Diyalog durumu belirsizliği politika girdisi olmalı; yanlış anlama geldiğinde eski yorum sürdürülmemeli, state geri alınmalı ve yeniden yorumlanmalıdır.

Kaynaklar:

- [Uncertainty Measures in Neural Belief Tracking](https://aclanthology.org/2021.emnlp-main.623/)
- [Handling Third Position Repair](https://aclanthology.org/2023.sigdial-1.52/)
- [Conversational Grounding](https://aclanthology.org/2024.lrec-main.352/)

### 6.5 RAG ve factual grounding

RAG ilgili dış bilgiyi modele sağlar; factual doğruluğu garanti etmez. RAG sistemleri de desteklenmeyen veya çelişkili cümleler üretebilir.

Karar:

```text
retrieval
  -> claim/evidence seçimi
  -> kontrollü içerik planı
  -> doğal gerçekleştirme
  -> atomik claim doğrulaması
```

Kritik şirket gerçeklerinde serbest RAG cevabı kullanılmamalıdır.

Kaynaklar:

- [Retrieval-Augmented Generation](https://papers.nips.cc/paper_files/paper/2020/hash/6b493230205f780e1bc26945df7481e5-Abstract.html)
- [RAGTruth](https://aclanthology.org/2024.acl-long.585/)
- [FaithDial](https://aclanthology.org/2022.tacl-1.84/)
- [FActScore](https://arxiv.org/abs/2305.14251)
- [RARR](https://aclanthology.org/2023.acl-long.910/)

### 6.6 Türkçe ve WhatsApp gürültüsü

Kısaltma, küçük/büyük harf, noktalama eksikliği, yazım hatası, birleşik kelimeler, morfolojik varyasyon, argo ve dil geçişi ana kabul testinin parçası olmalıdır.

MASSIVE Türkçeyi içeren geniş çok dilli intent/slot verisi sunar; fakat Atlas Metal’in gerçek çok turlu pragmatiği için alan-özel corpus gereklidir.

Kaynaklar:

- [MASSIVE](https://aclanthology.org/2023.acl-long.235/)
- [Real-World Noise Robustness](https://aclanthology.org/2021.nlp4convai-1.7/)
- [Multilingual TOD Performance Disparities](https://aclanthology.org/2023.emnlp-main.422/)
- [Show, Don’t Tell](https://aclanthology.org/2022.naacl-main.336/)

## 7. Qwen3 modelinin rolü

Qwen3 8B’nin uygun görevleri:

- Gürültülü ve kısa Türkçeyi yorumlamak.
- Konuşma edimi, referent, amaç hipotezi ve state delta önermek.
- İzinli yüksek seviye komutları üretmek.
- Seçilmiş içerik planını doğal Türkçede gerçekleştirmek.

Uygun olmayan otorite görevleri:

- Şirket gerçeğini kendi parametrik belleğinden belirlemek.
- İzinli iş eylemlerine tek başına karar vermek.
- Kendi confidence değerini doğruluk kanıtı saymak.
- Kullanıcı veya retrieval talimatlarına dayanarak güvenlik politikasını değiştirmek.
- Serbest factual clause ile kritik şirket claim’i eklemek.

Ollama structured output yalnızca biçimi güçlendirir; doğru referent, amaç veya komut seçimini garanti etmez.

Kaynaklar:

- [Qwen3](https://qwenlm.github.io/blog/qwen3/)
- [Qwen3 GitHub](https://github.com/QwenLM/Qwen3)
- [Qwen Function Calling](https://github.com/QwenLM/Qwen3/blob/main/docs/source/framework/function_call.md)
- [Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs)

Fine-tuning başlangıç adımı değildir. Önce altın değerlendirme seti ve hata kümeleri oluşturulmalıdır. Tekrarlayan hatalar model kapasitesi/semantik genelleme kaynaklıysa şirket gerçekleri yerine komut üretimi, pragmatik yorum ve Türkçe gürültü dayanıklılığı üzerinde SFT düşünülebilir.

## 8. Güvenlik kararı

Kullanıcı mesajı ve retrieval’dan gelen bütün içerik güvenilmeyen veridir; sistem talimatı değildir.

İlkeler:

- LLM komutları öneridir; trusted runtime doğrular.
- Kullanıcı metni flow veya şirket politikası tanımlayamaz.
- Retrieval metni program akışını değiştiremez.
- Araçlar açık capability ve minimum yetkiyle çağrılır.
- Claim ve tool argümanları allowlist/schema ile doğrulanır.
- Prompt injection bir system prompt cümlesiyle çözülmüş sayılmaz.

Kaynaklar:

- [Indirect Prompt Injection](https://arxiv.org/abs/2302.12173)
- [AgentDojo](https://proceedings.nips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html)
- [StruQ](https://www.usenix.org/conference/usenixsecurity25/presentation/chen-sizhe)
- [CaMeL](https://arxiv.org/abs/2503.18813)

## 9. Değerlendirme sözleşmesi

### 9.1 Altın test etiketi

Beklenen cevap tam bir cümle olarak yazılmamalıdır. Her senaryo şu alanlarla etiketlenmelidir:

- Turn ve konuşma hedefi.
- Referent.
- Konuşma edimi veya çoklu edimler.
- Entity/slot değerleri.
- Turn öncesi ve sonrası diyalog durumu.
- Beklenen eylem/komutlar.
- Zorunlu ve yasak claim’ler.
- Kullanılabilir evidence/claim ID’leri.
- Belirsizlik ve handoff gereksinimi.
- Başarılı konuşma son-durum koşulları.
- Güvenlik ve risk etiketi.

Bu sayede farklı doğal cümleler kabul edilirken semantik hata kesin biçimde ölçülebilir.

### 9.2 Davranışsal test matrisi

- Kimlik ve referent: `sen nesin`, `şirket ne yapıyor`, `bu sistem ne`.
- Kısa ve eksik mesaj: `ne`, `sen ne`, `o ne`, `hmm`, `tamam`.
- Paraphrase/typo invariance: `sen nesin`, `sen nesn`, `sen neysin`.
- Selamlama: `sa`, `slm`, emoji, yalnız `merhaba`.
- Bilinen ürün/fiyat/teslimat.
- Bağlamsal takip: `peki teslimat?`, `o ne kadar?`.
- Düzeltme: `fiyat değil teslimat`, `AX-500 değil BX-300`.
- Çoklu niyet ve konu değişimi.
- Discovery reddi ve konuşma kapanışı.
- Bilinmeyen/OOS ürün ve özellik.
- Çelişkili veya eski şirket bilgisi.
- Prompt injection ve gizlilik.
- Uzun konuşmada bilgi güncelleme ve unutma.

CheckList formatında:

- **Minimum functionality:** `sen nesin` doğru referent ve kimlik eylemini seçmeli.
- **Invariance:** noktalama ve makul typo semantik sonucu değiştirmemeli.
- **Directional expectation:** ürün bilinirden bilinmeyene değiştiğinde eylem `ANSWER`dan `CLARIFY/HANDOFF`a dönmeli.

### 9.3 Metrikler

- Dialogue-act doğruluğu.
- Referent accuracy.
- Intent/goal macro-F1.
- Slot F1 ve turn-level state accuracy.
- State overwrite/repair doğruluğu.
- Paraphrase-group pass rate.
- Clarification precision/recall ve gereksiz soru oranı.
- Goal drift oranı.
- Journey/task son-durum başarısı.
- Claim faithfulness ve answer completeness.
- Unsupported atomic claim oranı.
- Handoff precision/recall.
- Error-recovery başarısı.
- `pass^k`: aynı senaryonun tekrarlı güvenilirliği.
- p50/p95 gecikme ve çözüm için tur sayısı.
- İnsan değerlendirmesinde doğallık, sıcaklık, güven, yararlılık ve baskı hissi.

Kritik ticari doğruluk ve güvenlik hard gate olmalıdır:

- Uydurulmuş fiyat, teslimat, stok veya ödeme bilgisi: sıfır tolerans.
- Yetkisiz işlem veya gizli veri sızıntısı: sıfır tolerans.
- Yüksek doğallık puanı kritik ihlali telafi edemez.

Kaynaklar:

- [CheckList](https://aclanthology.org/2020.acl-main.442/)
- [Schema-Guided Dialogue Dataset](https://arxiv.org/abs/1909.05855)
- [DialoGLUE](https://arxiv.org/abs/2009.13570)
- [CLINC150/OOS](https://aclanthology.org/D19-1131/)
- [FED](https://aclanthology.org/2020.sigdial-1.28/)
- [τ-bench](https://arxiv.org/abs/2406.12045)
- [CRMArena-Pro](https://arxiv.org/abs/2505.18878)

### 9.4 Karşılaştırmalı deney

Aynı yerel model ve aynı senaryolarla üç sistem karşılaştırılmalıdır:

1. Mevcut büyük-prompt/serbest üretim baseline’ı.
2. Açık state + komut planı + claim gate.
3. İkinci sistem + bağımsız fact/policy verifier.

Her senaryo çoklu tekrarda çalıştırılmalıdır. Böylece iyileşmenin modelden mi, mimariden mi, verifier’dan mı geldiği ayrılabilir.

## 10. Uygulama öncesi yol haritası

1. **Tamamlandı — davranış sözleşmesi:** Ortak amaç, state alanları, izinli komutlar, journey’ler ve repair politikaları normatif ontoloji ile transition spesifikasyonunda kesinleştirildi.
2. **Uygulama sırasında — şirket ontolojisi:** Claim, kapsam, geçerlilik, negative/unknown ve handoff politikaları mevcut fixture dışındaki gerçek şirket verisi için tamamlanır.
3. **Tamamlandı — altın Türkçe corpus v1:** Kısa girdiler, typo/paraphrase grupları ve çok turlu senaryoları içeren 55 seed senaryo hazırlandı.
4. **Sonraki uygulama adımı — baseline ölçümü:** Mevcut sistem bütün metriklerde corpus'a karşı kaydedilir.
5. **Mimari karşılaştırma:** Parlant ve CALM-benzeri TypeScript/LangGraph yaklaşımı küçük ama aynı kapsamlı testte değerlendirilir.
6. **Model bake-off:** mimari sabitken yerel modeller komut doğruluğu, doğallık ve gecikmede karşılaştırılır.
7. **Hata analizi:** schema, state, policy, evidence, realization ve model kapasitesi hataları ayrı kümelenir.
8. **Gerekirse fine-tuning:** yalnızca tekrarlayan pragmatik/komut hataları için uygulanır.
9. **Shadow değerlendirme:** gerçek müşteriye bağlanmadan önce çok turlu ve güvenlik testleri tekrarlanır.
10. **Üretim izleme:** goal drift, unsupported claim, over/under-handoff, repair başarısı ve gecikme izlenir.

## 11. Nihai karar kaydı

Bu proje artık aşağıdaki ilkelere göre ilerlemelidir:

1. Her kısa cümle için ayrı regex, prompt veya cevap kuralı yazılmayacak.
2. Tek intent etiketi yerine konuşma edimi + referent + amaç hipotezi + state delta kullanılacak.
3. LLM doğrudan iş mantığını ve şirket gerçeğini belirlemeyecek; izinli komutlar önerecek.
4. Diyalog durumu ve ortak zemin kalıcı, açık ve denetlenebilir olacak.
5. Flow’lar cümle dizileri değil, iş amaçları ve izinli geçişler olarak tanımlanacak.
6. `CLARIFY` ve `REPAIR` fallback değil, birinci sınıf diyalog eylemleri olacak.
7. Şirket claim grafiği factual otorite olmaya devam edecek.
8. Doğal Türkçe üretim, içerik ve kanıt seçildikten sonra yapılacak.
9. Framework seçimi altın Türkçe değerlendirme setinden sonra verilecek.
10. Kritik doğruluk ve güvenlik metrikleri hard gate olacak.

En kısa özet:

> Aranan “bilinç”, daha uzun prompt değildir. Kalıcı amaç, ortak zemin, referent çözümü, açık plan, kanıt ve onarım döngüsüdür. `ne` veya `sen ne` gibi kısa mesajlar da bu döngüye girer; sistem bağlam yeterliyse anlamlandırır, bağlam yetersizse doğal ve kısa bir repair/clarification ile güvenli biçimde ilerler.
