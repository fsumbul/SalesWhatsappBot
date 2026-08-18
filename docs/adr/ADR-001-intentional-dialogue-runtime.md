# ADR-001: Amaç Yönelimli Diyalog Çekirdeğinin Uygulama Yaklaşımı

- **Durum:** Kabul edildi
- **Tarih:** 2026-08-03
- **Karar sahipliği:** SalesWhatsappBot mimarisi
- **Kapsam:** Müşteri simülatörü ve gelecekteki müşteri-facing diyalog runtime'ı
- **İlgili araştırma:** [Amaç Yönelimli ve Kanıta Bağlı Diyalog Ajanı](../intentional-dialogue-architecture-research-2026-08-03.md)
- **İlgili sözleşmeler:** [Davranış ontolojisi](../dialogue-behavior-ontology-v1.md), [State-transition ve repair](../dialogue-state-transition-repair-spec-v1.md), [Türkçe corpus annotation rehberi](../turkish-dialogue-gold-corpus-annotation-guide-v1.md)

## 1. Karar

İlk üretim adımında mevcut trusted TypeScript runtime, **Rasa CALM'den esinlenen fakat framework-bağımsız** bir diyalog çekirdeğine dönüştürülecektir.

İlk aşamada:

- Rasa CALM runtime'ına geçilmeyecek.
- Parlant üretim bağımlılığı olarak eklenmeyecek.
- LangGraph.js zorunlu orchestration katmanı olarak eklenmeyecek.
- Mevcut TypeScript claim/evidence güvenlik sınırı korunacak.
- LLM doğrudan cevap ve iş mantığı otoritesi değil, typed semantic proposal ve command generator olarak kullanılacak.
- Diyalog state'i, flow/journey stack'i, transition'lar, claim seçimi ve hard gate'ler trusted TypeScript tarafından yürütülecek.
- Çekirdek, ileride LangGraph.js veya başka bir yürütücüye taşınabilecek açık port/adaptör sınırlarıyla tanımlanacak.

Parlant, ana uygulamaya eklenmeden önce Türkçe altın corpus üzerinde ayrı bir karşılaştırma deneyi adayıdır. LangGraph.js ise ancak durable execution, uzun süreli checkpoint, paralel workflow veya kapsamlı human-in-the-loop ihtiyacı mevcut çekirdeği aşarsa yeniden değerlendirilecektir.

## 2. Bağlam

Mevcut sistemin güçlü tarafları:

- Next.js/TypeScript müşteri simülatörü hazırdır.
- Yerel Qwen3, Ollama üzerinden erişilmektedir.
- Şirket JSON grafiği factual otoritedir.
- Claim ID doğrulama, literal kritik gerçek render etme ve fail-closed davranışı bulunmaktadır.
- Kullanıcı, prompt veya yüzey cümle bazlı ad hoc çözümleri açıkça reddetmiştir.

Eksik olanlar bir orchestration kütüphanesinden önce semantik sözleşmelerdir:

- Bileşimsel konuşma edimi ve referent modeli.
- Kalıcı ortak zemin ve kullanıcı amacı hipotezleri.
- İzinli komutlar.
- Flow/journey state'i.
- Correction, rollback, repair, interruption ve resume semantiği.
- Türkçe kısa/eksik mesajlar için davranışsal evaluation corpus'u.

Bir framework bu semantiği otomatik olarak doğru tanımlamaz. Özellikle LangGraph state yürütür, fakat state'in ne anlama geldiğini belirlemez.

## 3. Karar sürücüleri

Öncelik sırasına göre sürücüler:

1. Şirket gerçeklerinde sıfır kritik hallucination hedefi.
2. Türkçe kısa, hatalı ve çok turlu mesajlarda pragmatik doğruluk.
3. Ad hoc phrase/intent kuralı üretmeden genellenebilirlik.
4. Mevcut claim-gate ve fail-closed davranışının yeniden kullanılması.
5. Yerel Qwen3/Ollama üzerinde tam model ve veri kontrolü.
6. Mevcut TypeScript uygulamasıyla düşük operasyonel karmaşıklık.
7. State, command, evidence ve kararların denetlenebilir olması.
8. Framework ve sağlayıcı lock-in'inin sınırlanması.
9. İleride journey sayısı ve araç kullanımı arttığında genişleyebilme.
10. Framework seçimini izlenime değil, ortak bir Türkçe benchmark'a dayandırma.

## 4. Değerlendirilen seçenekler

### A. Rasa CALM runtime'ına geçiş

CALM, LLM'nin kullanıcı mesajını tek intent yerine komut dizisine çevirmesi, FlowPolicy'nin iş mantığını yürütmesi ve correction/clarification/topic-change gibi pattern'ları yönetmesi nedeniyle kavramsal olarak en güçlü referanstır.

Avantajları:

- Aranan command-generator/flow-policy ayrımı hazırdır.
- Repair ve conversation pattern'ları birinci sınıf kavramlardır.
- Küçük modellerle command generation hedeflenmiştir.
- Diyalog kararları serbest ReAct metninden daha denetlenebilirdir.

Dezavantajları:

- Güncel CALM özellikleri lisans anahtarı gerektiren Rasa Platform/Developer Edition kapsamındadır.
- Ücretsiz Developer Edition güncel belgelerde dış müşteri konuşmaları için aylık sınır belirtmektedir.
- Python tabanlı ayrı runtime ve deployment yüzeyi getirir.
- Mevcut TypeScript claim-gate'in taşınması veya servis sınırından çağrılması gerekir.
- Şirket-özel ontoloji ve Türkçe pragmatik corpus yine ayrıca tasarlanmalıdır.

Kaynaklar:

- [CALM](https://rasa.com/docs/learn/concepts/calm/)
- [Command Generator](https://rasa.com/docs/pro/customize/command-generator/)
- [Flow Policy](https://rasa.com/docs/reference/config/policies/overview/)
- [Rasa lisanslama](https://rasa.com/docs/pro/installation/licensing/)
- [Rasa fiyatlandırma ve Developer Edition sınırları](https://rasa.com/pricing)

### B. Parlant'a geçiş

Parlant müşteri-facing interaction control için guidelines, relationships, glossary, journeys ve tracing sunar.

Avantajları:

- Müşteri hizmetleri ve kontrollü B2B/B2C konuşmalarına doğrudan odaklanır.
- Adaptive journey modeli konu sapması, geri dönme ve adım atlamaya uygundur.
- Apache 2.0 lisanslıdır.
- Guideline ilişkileri ve tracing davranış kontrolünü görünür yapabilir.
- Natural/canned/strict composition seçenekleri claim yaklaşımına yakındır.

Dezavantajları:

- Python servis ve yeni operasyonel yüzey getirir.
- Proje mevcut sistemden daha gençtir; üretici iddialarının proje-özel Türkçe Qwen3 kanıtı yoktur.
- Guidelines semantik seviyede tutulmazsa ad hoc kural yığınına dönüşebilir.
- Mevcut claim graph ve trusted transition semantiği için adaptasyon gerekir.
- Yerel Qwen3 8B ile command/journey uyumu ölçülmeden risk bilinemez.

Kaynaklar:

- [Parlant GitHub ve Apache 2.0 lisansı](https://github.com/emcie-co/parlant)
- [Parlant Journeys](https://dev.parlant.io/docs/concepts/customization/journeys/)
- [Parlant Glossary](https://dev.parlant.io/docs/concepts/customization/glossary/)

### C. LangGraph.js üzerinde özel diyalog çekirdeği

LangGraph.js state, node/edge, persistence ve human-in-the-loop için genel bir yürütme katmanıdır.

Avantajları:

- TypeScript ve mevcut teknoloji yığınıyla uyumludur.
- MIT lisanslıdır.
- Checkpoint, pause/resume ve karmaşık graph ihtiyacında hazır yetenekler sağlar.
- Model ve sağlayıcı seçimini kısıtlamaz.

Dezavantajları:

- Diyalog edimi, referent, ortak zemin, journey, repair ve claim semantiğini sağlamaz.
- Basit bir state machine için erken eklenirse gereksiz soyutlama ve bağımlılık getirir.
- Framework graph'ının doğru olması, diyalog politikasının doğru olduğu anlamına gelmez.
- Debug ve persistence yaklaşımını proje şekline göre ayrıca standardize etmek gerekir.

Kaynaklar:

- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph.js Graph API](https://docs.langchain.com/oss/javascript/langgraph/graph-api)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph MIT lisansı](https://github.com/langchain-ai/langgraph/blob/main/LICENSE)

### D. Framework-bağımsız typed TypeScript çekirdek

Mevcut runtime; formal ontology, state ledger, validated command plan, journey stack, evidence gate ve verifier bileşenleriyle genişletilir.

Avantajları:

- Mevcut factual güvenlik sınırı doğrudan korunur.
- Yeni servis, dil veya lisans anahtarı gerekmez.
- Semantik sözleşme framework API'sine göre değil ürün ihtiyacına göre kurulur.
- Yerel Qwen3 ve Ollama çağrıları değişmeden kontrol edilebilir.
- Kısa vadede en düşük operasyonel ve migration maliyetine sahiptir.
- Corpus testleri bileşen sınırlarında deterministik çalıştırılabilir.

Dezavantajları:

- Flow stack, persistence, tracing ve tooling'in bir kısmı proje tarafından geliştirilir.
- Kontrolsüz büyürse özel framework bakım yüküne dönüşebilir.
- Semantik çekirdek ile transport/UI sınırlarının disiplinli korunması gerekir.
- Gelecekte karmaşık parallel workflow veya uzun süreli durable execution ihtiyacında LangGraph benzeri bir katman gerekebilir.

## 5. Ağırlıklı karar matrisi

Puanlar 1 (zayıf) ile 5 (çok güçlü) arasındadır. Bunlar evrensel ürün puanları değil; bu projenin mevcut durumu için mimari değerlendirmedir.

| Kriter | Ağırlık | Rasa CALM | Parlant | LangGraph.js + özel semantik | Typed TS çekirdek |
|---|---:|---:|---:|---:|---:|
| Diyalog semantiğine hazır uyum | 20 | 5 | 4 | 2 | 4 |
| Mevcut TypeScript stack uyumu | 15 | 2 | 2 | 5 | 5 |
| Claim-gate'i koruma kolaylığı | 15 | 3 | 3 | 5 | 5 |
| Yerel Qwen3/Ollama kontrolü | 10 | 4 | 3 | 5 | 5 |
| Operasyonel sadelik | 10 | 2 | 2 | 3 | 5 |
| Lisans/lock-in | 10 | 2 | 5 | 5 | 5 |
| Hazır tracing/repair tooling | 10 | 5 | 4 | 4 | 2 |
| Mevcut risk altında teslim hızı | 10 | 2 | 2 | 3 | 5 |
| **Ağırlıklı toplam / 5** | **100** | **3,30** | **3,25** | **4,00** | **4,55** |

Matrisin sonucu, kavramsal lider ile uygulama kararının farklı olabileceğini gösterir:

- **Kavramsal referans:** Rasa CALM.
- **Hazır karşılaştırma adayı:** Parlant.
- **İleride genel yürütücü adayı:** LangGraph.js.
- **Şimdiki uygulama kararı:** Framework-bağımsız typed TypeScript çekirdek.

## 6. Seçilen çekirdeğin zorunlu sınırları

Typed TypeScript yaklaşımı “her şeyi tek dosyada kendimiz yazalım” anlamına gelmez. Aşağıdaki bağımsız portlar korunmalıdır:

1. **Interpreter portu:** Mesaj + sınırlı diyalog bağlamı → semantic proposal.
2. **State reducer:** Eski state + doğrulanmış event → yeni state.
3. **Policy portu:** State + journeys + risk → izinli command plan.
4. **Evidence portu:** Doğrulanmış bilgi ihtiyacı → claim/evidence seti.
5. **Realizer portu:** İçerik planı + izinli claim'ler → doğal aday cevap.
6. **Verifier portu:** Cevap + state + commands + evidence → pass/repair/handoff.
7. **Trace portu:** Her kararın girdi, çıktı, kaynak ve reddetme nedenlerini kaydetme.
8. **Persistence portu:** State snapshot/event log; ilk aşamada basit, ileride değiştirilebilir.

Bu portlar framework'e taşınabilirliği sağlar ve custom runtime'ın monolite dönüşmesini engeller.

## 7. Kabul koşulları

Karar, yalnızca aşağıdaki koşullarla başarılı kabul edilir:

- Ontolojideki bütün semantic proposal ve command'lar şema ile doğrulanabilir.
- State değişimi yalnızca trusted reducer üzerinden gerçekleşir.
- LLM herhangi bir claim ID, entity ID veya transition'ı keyfi olarak yürürlüğe koyamaz.
- Kritik factual claim'ler yalnızca onaylı store'dan gelir.
- `ne`, `sen ne`, typo, correction ve topic switch testleri phrase-specific production kuralı olmadan geçer.
- Her tur için interpreter, reducer, policy, evidence ve verifier izi ayrıştırılabilir.
- Türkçe altın corpus baseline ve yeni çekirdek üzerinde tekrarlı çalıştırılabilir.
- Kritik unsupported claim ve yetkisiz eylem oranı sıfırdır.

## 8. Reddedilen yaklaşımlar

### Tek büyük system prompt

İş mantığı, factual authority, ton ve diyalog durumunu tek promptta birleştirir; kararları denetlenemez hale getirir. Reddedildi.

### Her ifade için intent/regex/cevap ekleme

`ne`, `sen ne`, `sa` gibi yüzey ifadelerine özel davranış eklemek veri dağılımını ezberletir ve yeni varyantlarda kırılır. Reddedildi.

### Serbest ReAct/tool-calling ajan

Kritik müşteri gerçekleri ve iş politikası için fazla serbesttir; tool çağrısı ve metin üretimini aynı örtük akıl yürütmeye bırakır. Reddedildi.

### Hemen fine-tuning

Formal hedef etiketi ve altın corpus olmadan hata türünü modele gömer; mimari kusuru veriyle maskeleyebilir. Reddedildi.

## 9. Sonuçlar ve ödünleşimler

Olumlu sonuçlar:

- Mevcut factual güvenlik yatırımı korunur.
- En hızlı biçimde ölçülebilir semantik çekirdek oluşturulur.
- Framework kararı gelecekte corpus kanıtına dayanabilir.
- Yerel model ve veri kontrolü sürer.

Maliyetler:

- State reducer, flow stack, trace ve test harness için proje içi mühendislik gerekir.
- Hazır conversation tooling başlangıçta sınırlı kalır.
- Sözleşme disiplininden sapılırsa özel framework borcu oluşabilir.

Risk azaltma:

- Ontoloji ve transition spesifikasyonu normatif kabul edilir.
- Her yeni command/journey önce corpus vakası ve invariant ile gerekçelendirilir.
- Phrase-specific kural eklenmesi mimari review gerektirir.
- Persistence ve orchestration portları framework adaptasyonuna açık tutulur.

## 10. Yeniden değerlendirme tetikleyicileri

Aşağıdakilerden biri gerçekleşirse karar yeniden açılır:

- Onlarca paralel/çakışan journey nedeniyle flow stack yönetimi kararsızlaşır.
- Günler süren oturumlarda durable execution ve tam checkpoint/resume gerekir.
- Çok sayıda tool çağrısı için insan onaylı pause/resume gerekir.
- Custom trace/debug tooling maliyeti hazır framework adaptasyonunu aşar.
- Parlant, aynı Türkçe corpus'ta anlamlı ve tekrarlanabilir kalite/teslim süresi üstünlüğü gösterir.
- Rasa CALM lisans ve operasyon koşulları proje ölçeğinde daha uygun hale gelir.
- Typed çekirdeğin bakım maliyeti ölçülmüş biçimde kabul eşiğini aşar.

## 11. Doğrulama deneyi

Framework kararı için ortak deney protokolü:

1. Aynı Qwen3 modeli ve sıcaklık politikası kullanılır.
2. Aynı şirket claim store'u kullanılır.
3. Aynı Türkçe altın corpus senaryoları çalıştırılır.
4. Aynı semantic label, state transition ve command beklentileri ölçülür.
5. Her senaryo çoklu tekrarda değerlendirilir.
6. Doğruluk yanında p50/p95 gecikme, trace açıklanabilirliği ve operasyonel karmaşıklık kaydedilir.
7. Kritik claim/güvenlik ihlali hard failure sayılır.

Bu deney yapılmadan “framework daha akıllı cevap veriyor” gözlemi mimari geçiş gerekçesi kabul edilmez.
