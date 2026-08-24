# Yerel LLM JSON E2E deneyi

Bu klasör, backend veya WhatsApp entegrasyonuna dokunmadan yerel Ollama
modelinin şirket JSON'u ile güvenli şekilde çalışmasını ölçer.

## Karar sözleşmesi

Model müşteriye metin yazmaz. Yalnızca aşağıdaki karar nesnesini üretir:

```json
{"action":"answer","fact_ids":["ax-300-price"]}
```

veya:

```json
{"action":"handoff","fact_ids":[]}
```

Her şirket konfigürasyonunda görünür fact kümesi `F_visible` olsun. Controller
şu değişmezleri doğrular:

```text
fact_ids ⊆ F_visible
action = answer  ⇒  1 ≤ |fact_ids| ≤ 3
action = handoff ⇒  |fact_ids| = 0
```

Sonrasında müşteri metni yalnızca onaylı JSON içinden deterministic olarak
üretilir:

```text
reply = join(customer_text[f] for f in canonical(fact_ids))
```

Böylece model yeni bir fiyat, teknik özellik veya politika cümlesi ekleyemez.
Model yanlış fact seçerse bu, ayrı ölçülen bir **relevance** hatasıdır; yeni
olgu uydurma değildir.

## Mock şirketler ve müşteri simülasyonları

`local_llm_json_e2e_fixtures.json` üç farklı şirket yapısı içerir:

- fiziksel ürün/üretim (fiyat, malzeme, üretim süresi),
- SaaS aboneliği (kullanıcı başı fiyat, koltuk limiti, deneme),
- profesyonel hizmet (süre, ücret, teslim biçimi).

Her birinde `customer_visible: false` olan gizli canary fact'ler vardır.
Temel E2E seti; doğrudan soru, paraphrase, çok-fact soru, bilinmeyen bilgi,
gizli bilgi talebi, prompt injection, çapraz şirket bilgisi ve bağlayıcı
hukuki talebi kapsar.

## Ölçülen metrikler

- Şema/karar sözleşmesi geçerliliği
- Yalnızca müşteri-görünür fact kullanımı
- Gizli fact güvenliği
- JSON'dan başka metin üretmeyen renderer
- Eylem doğruluğu
- Fact seçimi precision, recall, F1 ve tam-küme doğruluğu
- Aynı girdide tekrarlanabilirlik
- Ortalama ve en yüksek model gecikmesi

Ek olarak gerçek modele dört metamorphic özellik uygulanır:

```text
f ⊥ q            ⇒ answer(K ∪ {f}, q) = answer(K, q)
answer(π(K), q)  = answer(K, q)
K[id] ← v₂       ⇒ render(K, id) yeni literal metni verir
id ∉ visible(KA) ⇒ id ∉ output_schema(KA)
```

Burada `K` şirket JSON'u, `π(K)` fact kayıtlarının yeniden sıralanmış hâli,
`q` müşteri sorusudur.

## Çalıştırma

Arch makinede, Ollama ve `qwen2.5:7b` çalışırken:

```bash
cd ~/llm-e2e
python3 local_llm_json_e2e.py --report e2e-report.json
```

Komut, ayrıntılı JSON raporu stdout'a ve belirtilen dosyaya yazar. Çıkış kodu
`0` ise tüm temel senaryolar ve metamorphic özellikler geçmiştir; `2` ise
güvenlik veya semantic seçim kalitesinde incelenmesi gereken en az bir sapma
vardır.

Bu deney eğitim/LoRA kullanmaz. Bir fiyat veya başka fact güncellendiğinde
yalnızca JSON değiştirilir ve deterministic renderer yeni literal metni hemen
kullanır.

## Yöneticiyle rehberli konfigürasyon deneyi

`guided_admin_config_e2e.py`, müşteri botundan ayrı olan yönetici kurulum
akışını test eder. Konfigürasyon konuşması, kalıcı şirket JSON'undan ayrı bir
oturum durum makinesidir:

```text
admin intent → session controller → proposal preview → explicit acceptance → draft
```

Bu test; ürün eklendikten sonra “başka ürün/hizmet var mı?” kontrolünün
tekrarlanmasını, açık koleksiyonun sessizce kapanmamasını, Excel/PDF
yüklemelerinin önce kanıtlı öneri olarak beklemesini, dosya metnindeki prompt
injection'ın talimat olmamasını, importtan sonra müşteri görünürlüğünün kapalı
kalmasını ve aynı event'in idempotent olmasını doğrular.

```bash
python3 guided_admin_config_e2e.py --report guided-admin-config-e2e-report.json
```

Serbest Türkçe admin mesajını sınıflandırmak için gerçek yerel Qwen'i sınayan
ayrı bir test de vardır:

```bash
python3 admin_intent_e2e.py --report admin-intent-e2e-report.json
```

Qwen yalnızca kapalı bir intent enum'u üretir. Controller, açık politika
override/publish enjeksiyonlarını `unknown`a düşürür; modelin sınıflandırması
tek başına taslağı değiştiremez, öneri kabul edemez veya yayınlayamaz.

## Niyet tabanlı interaktif yüzey deneyi

`interaction_policy_e2e.py`, aynı semantik hedefin kanal yeteneğine göre
buton, liste, Flow, belge istemi veya metin fallback'ine derlenmesini test
eder. LLM/UI etiketleri hiçbir zaman yetki taşımaz: yalnızca server-side
oturum, admin, taslak revizyonu ve soru kimliğine bağlı opak action ref geçiş
başlatabilir.

```bash
python3 interaction_policy_e2e.py --report interaction-policy-e2e-report.json
```

Test; üç seçenekli ürün devamını reply button, beşli seçimi liste, çok alanlı
fiyat girişini Flow, Excel/PDF isteğini belge prompt'u, 17 satırlı incelemeyi
Flow/sayfalı fallback olarak doğrular. Ayrıca sahte, eski, başka admin'e ait
ve izin dışı `publish` action'larını reddeder; yinelenen webhook action'ını
idempotent no-op yapar.

## Evrensel şirket akışı totality deneyi

`universal_config_flow_e2e.py`, sorulan alanların yalnızca ürün kataloğu ile
sınırlı kalmadığını mekanik olarak denetler. `CompanyAgentConfig` çekirdeğinde
adminin düzenleyebildiği her semantik yolun tam olarak bir `FlowSpec` sahibi
ve bir tipli reducer'ı olması gerekir; yeni bir alan eklenip akış tanımı veya
reducer eklenmezse session kurulumu/derleme başarısız olur.

Kayıtlı domain modülleri de açık bir sınırdır: doğru sürümde sağlayıcı,
doğrulayıcı, runtime-adapter kimliği ve `config` kökünü sahiplenen bir akış
tanımı olmadan kapsanmış sayılmazlar. Sağlayıcının hata vermesi de güvenli
şekilde görünür bir blokaja dönüşür.

```bash
PYTHONPATH=apps/api python3 universal_config_flow_e2e.py
```

E2E ayrıca marketplace hedef grafiğini accepted typed event'lerle birebir
yeniden kurar; katalogsuz klinikte profil alanı, süreç geçişi, policy-template
referansı ve kayıtlı modül config'ini staged graph üzerinden materialize eder.
Önizlemenin mutasyon yapmadığını, action ref'in admin/revision'a bağlı
olduğunu, duplicate accept'in no-op kaldığını ve açık koleksiyonun uzak adıma
atlamayı engellediğini de dener. Ayrıntılı matematiksel sözleşme için
[`docs/universal-configuration-flow.md`](../docs/universal-configuration-flow.md)
dosyasına bakın.
