# Diyalog Davranış Ontolojisi v1

**Sürüm:** `1.0.0`
**Tarih:** 2026-08-03
**Durum:** Normatif sözleşme
**Kapsam:** Uygulamadan, framework'ten, modelden, kanaldan ve sektörden bağımsız amaç yönelimli diyalog davranışı

Bu belge, bir diyalog ajanının kullanıcı mesajını nasıl temsil edeceğini, konuşma durumunu nasıl değiştireceğini, hangi eylemleri önerebileceğini ve factual içerikleri hangi epistemik koşullarda söyleyebileceğini tanımlar. Belge müşteri cevabı şablonları tanımlamaz. Yüzey cümlesi yerine davranışsal anlamı ve trusted runtime tarafından uygulanacak doğrulanabilir sözleşmeyi tanımlar.

Ana araştırma ve mimari gerekçe [Amaç Yönelimli ve Kanıta Bağlı Diyalog Ajanı — Araştırma ve Mimari Kararı](./intentional-dialogue-architecture-research-2026-08-03.md) belgesindedir. Bu ontoloji, o belgedeki kavramsal kararı normatif veri ve davranış sözleşmesine dönüştürür.

## 1. Normatif dil

Bu belgede:

- **ZORUNLU / MUST:** Uyumlu bir sistemin istisnasız yerine getirmesi gereken kuraldır.
- **YASAK / MUST NOT:** Uyumlu bir sistemin hiçbir koşulda yapmaması gereken davranıştır.
- **BEKLENİR / SHOULD:** Güçlü biçimde önerilen varsayılan davranıştır; sapma gerekçesi izlenebilir olmalıdır.
- **BEKLENMEZ / SHOULD NOT:** Yalnız açık ve izlenebilir bir gerekçeyle yapılabilecek davranıştır.
- **OLABİLİR / MAY:** İsteğe bağlı, uyumlu bir genişletmedir.

Alan çoklukları şu gösterimi kullanır:

- `1`: tam bir değer
- `0..1`: isteğe bağlı tek değer
- `0..*`: sıfır veya daha fazla değer
- `1..*`: en az bir değer

Kanonik alan adları bu belgenin semantik referansıdır. Bir uygulama farklı bir serileştirme kullanabilir; ancak semantik kayıp olmadan bu kanonik modele çift yönlü dönüşüm sağlayabilmelidir.

## 2. Kapsam ve anti-hedefler

Ontoloji şu ayrımları birinci sınıf kabul eder:

1. Kullanıcı mesajı ile mesajın pragmatik yorumu.
2. Yorum önerisi ile doğrulanmış diyalog durumu.
3. Kullanıcı amacı ile sistemin bir sonraki eylemi.
4. Diyalog eylemi ile doğal dildeki gerçekleştirmesi.
5. Retrieval sonucu ile onaylı evidence.
6. Model confidence değeri ile epistemik doğruluk.
7. Journey'nin iş mantığı ile müşteri cümlelerinin sırası.

Bu ontolojinin anti-hedefleri şunlardır:

- Her yüzey mesajına ayrı intent, regex veya cevap tanımlamak.
- Ham sohbet geçmişini diyalog durumu saymak.
- Modelin serbest metnini doğrudan state değişikliği veya iş eylemi olarak yürütmek.
- Yüksek model confidence değerini factual doğruluk kanıtı saymak.
- Retrieval içeriğini otomatik olarak güvenilir veya talimat yetkisine sahip kabul etmek.
- Journey'yi kullanıcıyı belirli bir cümle dizisine zorlayan senaryo olarak tanımlamak.
- Doğallık, sıcaklık veya akıcılığın kritik doğruluk ihlalini telafi etmesine izin vermek.

## 3. Üst düzey bilgi modeli

Bir diyalog turu aşağıdaki mantıksal zinciri izler:

```text
RawTurn
  -> TurnInterpretation
  -> validated StateDelta
  -> DialogueState
  -> ActionPlan
  -> EvidenceSelection
  -> ContentPlan
  -> SurfaceRealization
  -> ValidationResult
```

Her aşama önceki aşamaya referans vermek ZORUNDADIR. Son müşteri cevabı en azından kaynak tur, kullanılan state revision, yetkilendirilmiş komutlar ve varsa evidence/claim kimliklerine kadar izlenebilir olmak ZORUNDADIR.

### 3.1 Kök entity'ler

| Entity | Rolü | Kalıcılık |
|---|---|---|
| `RawTurn` | Kullanıcının veya sistemin değiştirilmemiş mesajı | Append-only |
| `TurnInterpretation` | Bir tur için bileşimsel semantik öneri | Append-only; revize edilebilir yeni kayıt |
| `DialogueState` | Doğrulanmış ortak zemin ve kontrol durumu | Revision'lı aggregate |
| `StateDelta` | State'e önerilen atomik değişiklik kümesi | Append-only event |
| `ActionPlan` | İzinli komutların sıralı planı | Append-only; durumlu |
| `JourneyDefinition` | Bir iş amacının mantıksal sözleşmesi | Sürümlü tanım |
| `JourneyInstance` | Journey'nin konuşmaya özgü çalışma durumu | Revision'lı entity |
| `Claim` | Atomik ve doğrulanabilir önerme | Sürümlü tanım |
| `EvidenceRecord` | Claim'i destekleyen, çürüten veya niteleyen kaynak | Sürümlü kayıt |
| `ContentPlan` | Gerçekleştirilecek davranış ve içerik iskeleti | Append-only |
| `ValidationResult` | Bir artefaktın normatif uygunluk sonucu | Append-only |

### 3.2 Ortak scalar ve value type'lar

| Type | Normatif anlam |
|---|---|
| `Identifier` | Boş olmayan, opaque ve kendi namespace'i içinde benzersiz kimlik |
| `OntologyVersion` | Bu belgenin SemVer biçimindeki sürümü |
| `Revision` | Monoton artan, negatif olmayan state sürümü |
| `Timestamp` | Zaman dilimi içeren mutlak zaman |
| `Locale` | BCP 47 uyumlu dil/yerel ayar etiketi |
| `TextSpan` | Raw mesaj içindeki başlangıç ve bitiş konumu; normalize metne değil raw mesaja bağlıdır |
| `PropositionId` | Bir konuşma önermesinin konuşma içindeki kalıcı kimliği |
| `ConfidenceScore` | Bölüm 10'da tanımlanan kalibre edilebilir güven değeri |
| `TraceRef` | Önceki yorum, state, delta, komut, claim veya evidence kaydına typed referans |

`RawTurn.text` değiştirilemez. Yazım düzeltmesi, transliterasyon veya normalizasyon ayrı bir `derived_text` alanı olarak tutulabilir; raw metnin yerine geçmesi YASAKTIR.

## 4. TurnInterpretation

`TurnInterpretation`, tek intent etiketi değildir. Bir mesajın eşzamanlı konuşma edimlerini, referent adaylarını, amaç hipotezlerini, entity/slot değerlerini, sosyal ve repair sinyallerini ve önerilen state değişikliğini taşır.

### 4.1 Zorunlu alanlar

| Alan | Çokluk | Kural |
|---|---:|---|
| `id` | 1 | Benzersiz interpretation kimliği |
| `ontology_version` | 1 | Yorumu üreten ontoloji sürümü |
| `conversation_id` | 1 | Konuşma kimliği |
| `turn_id` | 1 | Kaynak `RawTurn` kimliği |
| `based_on_state_revision` | 1 | Yorum sırasında okunan state revision |
| `language` | 1 | Algılanan veya varsayılan locale |
| `dialogue_acts` | 1..* | En az bir act; anlam çıkarılamıyorsa `UNKNOWN` |
| `referents` | 0..* | Açık veya örtük referent çözümlemeleri |
| `goal_hypotheses` | 1..* | En az bir amaç; anlam çıkarılamıyorsa `UNKNOWN` |
| `entities` | 0..* | Entity mention ve normalize değerler |
| `ambiguity` | 1 | Belirsizlik değerlendirmesi |
| `social_signals` | 0..* | Sosyal/duygulanımsal işaretler |
| `repair_signals` | 0..* | Önceki yorumu reddetme veya düzeltme işaretleri |
| `proposed_state_delta_id` | 0..1 | Doğrulama bekleyen delta referansı |
| `trace` | 1 | Üretici, zaman ve kullanılan bağlam bilgisi |

Bir `TurnInterpretation` doğrudan authoritative state değildir. Trusted runtime tarafından doğrulanmadan state'i veya journey'yi değiştirmesi YASAKTIR.

## 5. Dialogue act ontolojisi

Bir tur birden fazla act taşıyabilir. Her `DialogueActAssertion` şu alanları taşır:

| Alan | Çokluk | Açıklama |
|---|---:|---|
| `type` | 1 | `DialogueActType` |
| `confidence` | 1 | Act'e özgü güven; global mesaj güveni değildir |
| `span` | 0..1 | Act'i tetikleyen raw text bölgesi |
| `target_referent_ids` | 0..* | Act'in yöneldiği referent'ler |
| `primary` | 1 | Turun baskın act'i olup olmadığı |
| `mutual_exclusion_group` | 0..1 | Birbirine alternatif act hipotezlerini gruplar |

### 5.1 `DialogueActType` enum'u

#### Sosyal ve ilişki edimleri

| Değer | Anlam |
|---|---|
| `GREET` | Konuşmayı veya sosyal teması selamla açma |
| `RETURN_GREETING` | Önceki selamlamaya karşılık verme |
| `ACKNOWLEDGE` | Alındığını/anlaşıldığını kısa biçimde gösterme |
| `BACKCHANNEL` | Yeni içerik eklemeden takip edildiğini gösterme (`hmm`, `anladım`) |
| `THANK` | Teşekkür etme |
| `APOLOGIZE` | Özür bildirme |

#### Bilgi ve görev edimleri

| Değer | Anlam |
|---|---|
| `ASK_IDENTITY` | Ajanın veya başka bir referentin kimliğini sorma |
| `ASK_CAPABILITY` | Bir aktörün ne yapabildiğini veya kapsamını sorma |
| `ASK_FACT` | Doğrulanabilir bir önerme veya değer sorma |
| `ASK_EXPLANATION` | Anlam, gerekçe veya daha açık anlatım isteme |
| `ASK_RECOMMENDATION` | Bir değerlendirme veya öneri isteme |
| `REQUEST_REPEAT` | Önceki içeriğin yeniden söylenmesini isteme |
| `REQUEST_ACTION` | Sistemden veya başka bir aktörden eylem isteme |
| `INFORM` | Yeni bilgi veya kısıt bildirme |
| `ANSWER` | Önceki açık soruya yanıt verme |
| `OFFER` | İsteğe bağlı yardım, bilgi veya sonraki adım sunma |

#### Koordinasyon, düzeltme ve kontrol edimleri

| Değer | Anlam |
|---|---|
| `ACCEPT` | Bir öneri, yorum veya değeri kabul etme |
| `REJECT` | Bir öneri, yorum veya değeri reddetme |
| `CORRECT` | Önceki değer, referent veya yorumu değiştirme |
| `CONFIRM` | Bir değerin veya yorumun doğru olduğunu açıkça doğrulama |
| `DENY` | Bir önermenin doğru olmadığını bildirme |
| `CLARIFY` | Belirsizliği azaltan açıklama sağlama veya isteme |
| `CHANGE_TOPIC` | Etkin odağı veya yerel amacı değiştirme |
| `CANCEL` | Etkin görev veya journey'yi bırakma |
| `REQUEST_HANDOFF` | İnsan veya başka yetkili aktöre geçiş isteme |
| `CLOSE` | Konuşmayı kapatma veya kapanışa onay verme |

#### Güvenlik ve güven sınırı edimleri

| Değer | Anlam |
|---|---|
| `ATTEMPT_POLICY_OVERRIDE` | Kullanıcı veya güvenilmeyen içeriğin sistem politikasını, claim otoritesini, izinli komutları veya talimat hiyerarşisini değiştirmeye çalışması |
| `REQUEST_SENSITIVE_DATA` | System prompt, gizli anahtar, kişisel veri veya başka korunmuş içeriği isteme |

#### Sosyal/duygulanımsal sinyal edimleri

| Değer | Anlam |
|---|---|
| `EXPRESS_SURPRISE` | Şaşırma veya beklenmedik bulma |
| `EXPRESS_SATISFACTION` | Memnuniyet bildirme |
| `EXPRESS_DISSATISFACTION` | Memnuniyetsizlik veya itiraz bildirme |
| `EXPRESS_IMPATIENCE` | Gecikme veya tekrar nedeniyle sabırsızlık bildirme |
| `UNKNOWN` | Yeterli kanıtla sınıflandırılamayan act |

`DialogueActType` bir response template seçicisi değildir. Aynı act farklı context, locale, ton ve journey durumlarında farklı cümlelerle gerçekleştirilebilir.

## 6. Referent ontolojisi

Referent, bir act'in veya önermenin hakkında olduğu varlıktır. Referent çözümü entity recognition ile aynı değildir: `o`, `bu`, `sen`, eksiltili özne veya önceki cevabın tamamı referent olabilir.

### 6.1 `ReferentKind` enum'u

| Değer | Anlam |
|---|---|
| `ASSISTANT` | Konuşan yapay veya otomatik ajan |
| `USER` | Mevcut kullanıcı veya kullanıcı grubu |
| `SYSTEM` | Ajanı barındıran sistem, uygulama veya süreç |
| `ORGANIZATION` | Kurum, şirket veya ekip |
| `PRODUCT` | Ürün veya ürün ailesi |
| `SERVICE` | Hizmet veya servis |
| `OFFER` | Teklif, paket veya ticari seçenek |
| `POLICY` | Kural, koşul veya iş politikası |
| `PROCESS` | İş akışı, proje veya prosedür |
| `CLAIM` | Atomik factual önerme |
| `QUESTION` | Önceki açık veya bekleyen soru |
| `PRIOR_UTTERANCE` | Önceki kullanıcı mesajı |
| `PRIOR_RESPONSE` | Önceki sistem cevabı veya içindeki bölüm |
| `JOURNEY` | Etkin görev/journey instance'ı |
| `EXTERNAL_ENTITY` | Ontolojinin yerleşik türleri dışında kalan varlık |
| `DEICTIC_UNKNOWN` | İşaret edilen fakat çözülemeyen nesne (`o`, `bu`) |
| `NONE` | Act'in referent gerektirmediği durum |

### 6.2 `ReferentResolutionStatus` enum'u

| Değer | Anlam |
|---|---|
| `RESOLVED` | Tek bir aday yeterli kanıtla seçildi |
| `AMBIGUOUS` | Birden fazla makul aday korunuyor |
| `UNRESOLVED` | Aday üretmek için yeterli bağlam yok |
| `CONFLICTED` | Konuşma kanıtları birbirini dışlayan adaylar doğuruyor |
| `INVALIDATED` | Önceki çözüm kullanıcı düzeltmesi veya repair ile geçersizleşti |

### 6.3 `ReferentResolution` entity'si

| Alan | Çokluk | Kural |
|---|---:|---|
| `id` | 1 | Çözüm kimliği |
| `mention_span` | 0..1 | Raw mesajdaki açık ifade; örtük referent'te boş olabilir |
| `kind` | 1 | Beklenen referent türü |
| `status` | 1 | Çözüm durumu |
| `candidates` | 0..* | Aday kimliği, türü, confidence ve dayanakları |
| `selected_candidate_id` | 0..1 | Yalnız `RESOLVED` durumda dolu olmalıdır |
| `focus_distance` | 0..1 | Focus stack üzerindeki göreli uzaklık |
| `evidence_refs` | 0..* | Mesaj, state, soru veya journey dayanakları |

`AMBIGUOUS`, `UNRESOLVED` veya `CONFLICTED` bir referent runtime tarafından sessizce `RESOLVED` durumuna çevrilemez. Seçim için ek context, kullanıcı doğrulaması veya bütün adaylarda aynı sonucu veren güvenli bir eylem gerekir.

## 7. Entity ve slot modeli

### 7.1 `EntityMention`

| Alan | Çokluk | Açıklama |
|---|---:|---|
| `id` | 1 | Mention kimliği |
| `type_id` | 1 | Domain veya çekirdek entity type kimliği |
| `span` | 1 | Raw mesaj konumu |
| `surface_form` | 1 | Raw metindeki ifade |
| `normalized_value` | 0..1 | Doğrulanmış veya önerilen normalize değer |
| `candidate_values` | 0..* | Alternatif normalize değerler |
| `confidence` | 1 | Mention/type eşleşme güveni |
| `source` | 1 | `EXPLICIT`, `INFERRED_CONTEXT`, `RESOLVED_REFERENCE` |

Domain entity türleri namespaced olmalıdır; örneğin `core:organization` veya `domain:product`. Bir sürümde tanımlanan type kimliği başka bir anlam için yeniden kullanılamaz.

### 7.2 `SlotAssignment`

Slot, journey veya goal parametresine bağlanan typed değerdir.

| Alan | Çokluk | Kural |
|---|---:|---|
| `slot_id` | 1 | Namespaced slot tanımı |
| `value` | 0..1 | Typed değer |
| `entity_mention_id` | 0..1 | Değerin kaynak mention'ı |
| `status` | 1 | `PROPOSED`, `CONFIRMED`, `REJECTED`, `UNKNOWN`, `CONFLICTED` |
| `confidence` | 1 | Değer eşleme güveni |
| `provenance` | 1..* | Kullanıcı turu, state veya yetkili kayıt referansı |

Model tarafından çıkarılan slot `PROPOSED` başlar. Şema, type, scope ve state geçişi doğrulanmadan `CONFIRMED` olamaz.

## 8. Goal hypothesis ontolojisi

Amaç tek etiket değildir. Aynı anda farklı zaman ufuklarında ve birbirini dışlamayan birden fazla amaç taşınabilir.

### 8.1 `GoalScope` enum'u

| Değer | Anlam |
|---|---|
| `TURN` | Yalnız mevcut mesajın pragmatik amacı |
| `LOCAL` | Birkaç turluk bilgi veya karar alt amacı |
| `JOINT_LONG_TERM` | Kullanıcı ve sistemin sürdürdüğü ortak üst amaç |

### 8.2 `GoalType` enum'u

| Değer | Anlam |
|---|---|
| `ESTABLISH_CONTACT` | Sosyal temas kurma veya sürdürme |
| `ORIENT_TO_ASSISTANT` | Ajanın kimliğini anlama |
| `UNDERSTAND_SCOPE` | Ajanın, sistemin veya sürecin kapsamını anlama |
| `SEEK_INFORMATION` | Bir konu hakkında factual bilgi edinme |
| `SEEK_EXPLANATION` | Anlam veya gerekçe edinme |
| `SEEK_RECOMMENDATION` | Uygun seçenek veya öneri edinme |
| `DISCOVER_OR_EXPRESS_NEED` | İhtiyacı oluşturma, ifade etme veya netleştirme |
| `COMPARE_OPTIONS` | Seçenekleri ölçütlere göre karşılaştırma |
| `MAKE_DECISION` | Bir tercih veya karar oluşturma |
| `COMPLETE_TASK` | Domain'e özgü bir işi sonuca ulaştırma |
| `CORRECT_UNDERSTANDING` | Yanlış state, değer veya referent'i düzeltme |
| `RESOLVE_AMBIGUITY` | Ortak zemindeki belirsizliği giderme |
| `ESCALATE_TO_HUMAN` | İnsan veya yetkili aktöre geçme |
| `END_INTERACTION` | Konuşmayı bitirme |
| `UNKNOWN` | Amaç için yeterli kanıt olmaması |

Domain'e özgü “fiyat öğrenme”, “teslimat öğrenme” gibi amaçlar yeni çekirdek enum değerleri olarak eklenmemelidir. Bunlar `SEEK_INFORMATION` goal'ının `subject`, `predicate`, entity ve slot parametreleriyle ifade edilmelidir.

### 8.3 `GoalStatus` enum'u

| Değer | Anlam |
|---|---|
| `HYPOTHESIZED` | Henüz doğrulanmamış amaç adayı |
| `ACTIVE` | Politikanın izlediği geçerli amaç |
| `SUSPENDED` | Konu değişimi veya alt journey nedeniyle bekletilen amaç |
| `SATISFIED` | Başarı koşulları karşılanmış amaç |
| `REJECTED` | Kullanıcı veya doğrulama tarafından reddedilmiş amaç |
| `ABANDONED` | Artık izlenmeyen, fakat geçmişte geçerli olmuş amaç |
| `INVALIDATED` | Hatalı yorum veya repair nedeniyle geçersizleşmiş amaç |

### 8.4 `GoalHypothesis`

| Alan | Çokluk | Kural |
|---|---:|---|
| `id` | 1 | Kalıcı goal kimliği |
| `scope` | 1 | Zaman ufku |
| `type` | 1 | Çekirdek goal türü |
| `status` | 1 | Goal durumu |
| `subject_referent_ids` | 0..* | Goal'ın konusu |
| `predicate` | 0..1 | Domain'e özgü namespaced amaç/predicate |
| `parameters` | 0..* | Slot assignment'lar |
| `success_conditions` | 0..* | Ölçülebilir son-durum koşulları |
| `confidence` | 1 | Goal'a özgü güven |
| `supporting_refs` | 1..* | Bu hipotezi destekleyen tur/state referansları |
| `conflicting_refs` | 0..* | Hipotezle çelişen kanıtlar |
| `mutual_exclusion_group` | 0..1 | Birbirine alternatif amaçlar için grup |

Birbiriyle çelişmeyen goal confidence değerlerinin toplamının `1` olması gerekmez. Aynı `mutual_exclusion_group` içindeki adaylar aynı kalibrasyon politikası altında karşılaştırılmalı ve gerekirse normalize edilmelidir.

## 9. DialogueState ve ortak zemin

`DialogueState`, ham transcript'in özeti değildir. Politika, referent çözümü, repair ve factual yanıt için trusted runtime tarafından kabul edilmiş denetlenebilir ortak zemindir.

### 9.1 `InteractionMode` enum'u

| Değer | Anlam |
|---|---|
| `IDLE` | Etkin yerel amaç yok |
| `SOCIAL_ORIENTATION` | Selamlama, kimlik ve kapsam yönelimi |
| `NEED_DISCOVERY` | İhtiyacı baskısız netleştirme |
| `INFORMATION` | Bilgi edinme/verme |
| `COMPARISON` | Seçenek kıyaslama |
| `DECISION` | Tercih oluşturma |
| `EXECUTION` | Yetkili bir işi gerçekleştirme |
| `OBJECTION` | İtiraz veya uyuşmazlık ele alma |
| `REPAIR` | Yanlış anlamayı onarma |
| `HANDOFF` | Başka aktöre geçiş |
| `CLOSING` | Konuşmayı kapatma |

### 9.2 `DialogueState` alanları

| Alan | Çokluk | Açıklama |
|---|---:|---|
| `conversation_id` | 1 | Aggregate kimliği |
| `ontology_version` | 1 | State'in uyduğu ontoloji sürümü |
| `revision` | 1 | Monoton state revision |
| `interaction_mode` | 1 | Etkin yüksek seviye mod |
| `focus_stack` | 0..* | En yeni odak en üstte olacak typed referent listesi |
| `active_referents` | 0..* | Konuşma düzeyinde doğrulanmış referent çözümlemeleri |
| `goals` | 1..* | En az bir joint veya `UNKNOWN` goal kaydı |
| `journey_instances` | 0..* | Etkin, askıda veya geçmiş journey'ler |
| `open_questions` | 0..* | Yanıt bekleyen typed sorular |
| `accepted_propositions` | 0..* | Kullanıcı ve sistemin ortak zeminde kabul ettiği önermeler |
| `rejected_propositions` | 0..* | Açıkça reddedilmiş önermeler |
| `commitments` | 0..* | Aktör, içerik, koşul ve durum içeren taahhütler |
| `preferences_and_constraints` | 0..* | Kullanıcıya ait, amaçla ilgili ve kaynaklı değerler |
| `knowledge_gaps` | 0..* | Bilinmediği veya kanıtı olmadığı doğrulanmış alanlar |
| `repair_context` | 0..1 | Aktif onarım kaydı |
| `last_applied_delta_id` | 0..1 | Son state event'i |
| `updated_at` | 1 | Son başarılı revision zamanı |

Kişisel veri yalnız açık amaç için gerekli olduğu ölçüde state'e alınmalıdır. Kullanıcıya ait çıkarımsal özellikler factual kullanıcı gerçeği olarak kaydedilemez.

## 10. Confidence ve ambiguity

### 10.1 `ConfidenceScore`

| Alan | Çokluk | Kural |
|---|---:|---|
| `value` | 1 | Sonlu ve `[0,1]` aralığında sayı |
| `origin` | 1 | Confidence kaynağı |
| `calibrated` | 1 | Geçerli bir değerlendirme setinde kalibre edilip edilmediği |
| `calibration_profile_id` | 0..1 | `calibrated=true` ise ZORUNLU |
| `band` | 1 | Politika eşiğinden türetilmiş bant |

### 10.2 `ConfidenceOrigin` enum'u

- `MODEL`
- `RULE`
- `RETRIEVAL`
- `HUMAN`
- `COMBINED`
- `UNKNOWN`

### 10.3 `ConfidenceBand` enum'u

- `LOW`
- `MEDIUM`
- `HIGH`
- `UNSPECIFIED`

Band eşikleri ontolojide sabitlenmez. Sürümlü bir politika profilinde tanımlanır. Farklı act, referent, goal, entity ve risk sınıfları farklı eşikler kullanabilir.

### 10.4 `AmbiguityType` enum'u

- `NONE`
- `LEXICAL`
- `REFERENTIAL`
- `GOAL`
- `TEMPORAL`
- `SCOPE`
- `CONFLICT`
- `INSUFFICIENT_CONTEXT`
- `MULTIPLE`

`AmbiguityAssessment`, tür, adaylar, confidence, çözüm için gerekli bilgi ve belirsizliğin cevap üzerindeki etkisini taşır.

Modelin kendi confidence değeri hiçbir zaman epistemik doğruluk kanıtı değildir. Özellikle kritik claim veya dış etki oluşturan komut yalnız `MODEL/HIGH` gerekçesiyle yetkilendirilemez.

## 11. StateDelta

State yalnız doğrulanmış `StateDelta` ile değiştirilebilir. Doğal dil cevabının, model düşünce metninin veya retrieval sonucunun state'i doğrudan değiştirmesi YASAKTIR.

### 11.1 `StateTargetType` enum'u

- `INTERACTION_MODE`
- `FOCUS_STACK`
- `ACTIVE_REFERENT`
- `GOAL`
- `JOURNEY_INSTANCE`
- `OPEN_QUESTION`
- `PROPOSITION`
- `COMMITMENT`
- `PREFERENCE_OR_CONSTRAINT`
- `KNOWLEDGE_GAP`
- `REPAIR_CONTEXT`

### 11.2 `DeltaOperationType` enum'u

- `ADD`
- `SET`
- `REPLACE`
- `REMOVE`
- `PUSH`
- `POP`
- `ACCEPT`
- `REJECT`
- `CONFIRM`
- `INVALIDATE`
- `SUSPEND`
- `RESUME`
- `COMPLETE`
- `ABORT`

### 11.3 `StateDeltaOperation`

| Alan | Çokluk | Kural |
|---|---:|---|
| `operation` | 1 | Delta operation türü |
| `target_type` | 1 | Değişen state bölümü |
| `target_id` | 0..1 | Var olan entity hedefleniyorsa ZORUNLU |
| `value` | 0..1 | Operation'ın typed yeni değeri |
| `preconditions` | 0..* | Uygulama öncesi doğrulanacak koşullar |
| `reason_refs` | 1..* | Kullanıcı turu, interpretation veya policy dayanağı |
| `confidence` | 1 | Önerilen değişikliğe özgü güven |
| `inverse_operation` | 0..1 | Güvenli geri alma tanımı; destekleniyorsa |

### 11.4 `StateDelta` alanları

| Alan | Çokluk | Kural |
|---|---:|---|
| `id` | 1 | Event kimliği |
| `ontology_version` | 1 | Delta'nın ontoloji sürümü |
| `conversation_id` | 1 | Hedef konuşma |
| `expected_revision` | 1 | Optimistic concurrency precondition |
| `operations` | 1..* | Sıralı ve birlikte atomik operation'lar |
| `proposed_by` | 1 | Model, policy, user-confirmation veya trusted component |
| `source_interpretation_id` | 0..1 | Kullanıcı turundan geliyorsa ZORUNLU |
| `validation_result_id` | 0..1 | Uygulanmadan önce başarılı sonuç ZORUNLU |

Delta ya bütünüyle uygulanır ve revision tam bir artar ya da hiç uygulanmaz. Kısmi uygulama YASAKTIR.

Kullanıcı correction'ı önceki model çıkarımını geçersizleştirebilir; fakat authoritative şirket claim'ini değiştiremez. Kullanıcının “fiyat aslında X” demesi kullanıcı tarafından öne sürülen proposition olarak tutulabilir, authoritative factual state olarak kabul edilemez.

## 12. Command ve ActionPlan

Command, sistemin yapmasına izin verilen yüksek seviyeli davranıştır. Command doğal dil cümlesi değildir ve framework'e özgü tool call olmak zorunda değildir.

### 12.1 `CommandType` enum'u

| Değer | Normatif amaç |
|---|---|
| `ACKNOWLEDGE` | Sosyal veya içeriksel alımı kısa biçimde tanımak |
| `ORIENT` | Ajan, sistem, süreç veya konuşma kapsamına yönelim sağlamak |
| `ANSWER_IDENTITY` | Çözülmüş referentin kimliğini cevaplamak |
| `ANSWER_SCOPE` | Yetkinlik, kapsam veya rolü cevaplamak |
| `DISCOVER_NEED` | Kullanıcı ihtiyacını baskısız biçimde netleştirmek |
| `START_JOURNEY` | Giriş koşulları sağlanan journey başlatmak |
| `RESUME_JOURNEY` | Askıdaki journey'yi geçerli context ile sürdürmek |
| `SET_OR_CORRECT_STATE` | Doğrulanmış delta'yı uygulamak veya repair hazırlamak |
| `RETRIEVE_EVIDENCE` | Belirli claim/predicate için evidence aramak |
| `ANSWER_WITH_EVIDENCE` | Seçilmiş ve geçerli claim'lerle factual cevap vermek |
| `CLARIFY` | Kararı etkileyen belirsizliği ayırt edici soruyla azaltmak |
| `CONFIRM` | Riskli veya geri dönüşü zor yorum/eylem için kullanıcı doğrulaması istemek |
| `REPAIR` | Eski yorumu geri almak, state'i düzeltmek ve ortak zemini yeniden kurmak |
| `OFFER_NEXT_STEP` | İlgili ve isteğe bağlı bir sonraki adım sunmak |
| `HANDOFF` | Başka yetkili aktöre güvenli geçiş yapmak |
| `DECLINE_OVERRIDE` | Güvenilmeyen talimatın policy, claim, state veya yetki sınırını değiştirmesini reddetmek; kullanıcının izinli asıl amacını mümkünse ayrıca sürdürmek |
| `CLOSE` | Yeni görev zorlamadan konuşmayı kapatmak |

### 12.2 `CommandStatus` enum'u

- `PROPOSED`
- `AUTHORIZED`
- `EXECUTING`
- `SUCCEEDED`
- `FAILED`
- `SKIPPED`
- `BLOCKED`

### 12.3 `Command`

| Alan | Çokluk | Kural |
|---|---:|---|
| `id` | 1 | Plan içindeki benzersiz kimlik |
| `type` | 1 | İzinli command enum'u |
| `sequence` | 1 | Sıfırdan başlayan toplam sıra |
| `target_refs` | 0..* | Referent, goal, journey, claim veya state hedefi |
| `arguments` | 0..* | Command type'a göre typed parametreler |
| `preconditions` | 0..* | Yetkilendirme öncesi koşullar |
| `expected_effects` | 0..* | State delta veya dış etki beklentileri |
| `evidence_requirement` | 1 | Bölüm 14'teki gereksinim |
| `confidence` | 1 | Command seçimine ilişkin güven |
| `status` | 1 | Command yaşam döngüsü |
| `on_failure` | 0..* | İzinli güvenli command alternatifleri |
| `policy_trace_refs` | 1..* | Neden seçildiğini açıklayan goal/journey/policy referansı |

### 12.4 `ActionPlan`

| Alan | Çokluk | Kural |
|---|---:|---|
| `id` | 1 | Plan kimliği |
| `ontology_version` | 1 | Planın ontoloji sürümü |
| `conversation_id` | 1 | Konuşma |
| `based_on_state_revision` | 1 | Planlanan state revision |
| `source_interpretation_id` | 1 | Planı tetikleyen yorum |
| `commands` | 1..* | Sıralı command listesi |
| `shared_goal_ids` | 1..* | Planın hizmet ettiği goal'lar |
| `validation_result_id` | 0..1 | Yürütme öncesi başarılı doğrulama ZORUNLU |

Bir mesaj birden fazla command üretebilir. Aynı plan içinde command sırası semantiktir:

- Correction veya repair varsa eski state'i geçersizleştiren `SET_OR_CORRECT_STATE`/`REPAIR`, eski state'e dayanan cevaplardan önce gelmelidir.
- `ANSWER_WITH_EVIDENCE`, gerekli claim'ler plan öncesinde hazır değilse başarılı `RETRIEVE_EVIDENCE` sonrasında gelmelidir.
- `CLOSE`, aynı kullanıcı turu açıkça yeni bir amaç da taşımıyorsa `DISCOVER_NEED`, `START_JOURNEY` veya satış yönelimli `OFFER_NEXT_STEP` ile birleştirilemez.
- `HANDOFF` öncesi yalnız handoff için gerekli minimum bilgi toplanabilir.

## 13. Journey ontolojisi

Journey, kullanıcı cümlelerinin sırasını değil bir iş amacının durumlarını, geçişlerini, gerekli bilgilerini ve başarı koşullarını tanımlar.

### 13.1 `JourneyStatus` enum'u

- `NOT_STARTED`
- `ACTIVE`
- `SUSPENDED`
- `COMPLETED`
- `ABORTED`
- `FAILED`

### 13.2 `InterruptionPolicy` enum'u

- `ALLOW_AND_SUSPEND`
- `ALLOW_AND_KEEP_ACTIVE`
- `REQUIRE_CONFIRMATION`
- `DENY_WITH_POLICY_REASON`

`DENY_WITH_POLICY_REASON` yalnız güvenlik, yetki veya geri dönüşü olmayan işlem gerekçesiyle kullanılabilir; kullanıcıyı satış akışında tutmak için kullanılması YASAKTIR.

### 13.3 `JourneyDefinition`

| Alan | Çokluk | Kural |
|---|---:|---|
| `id` | 1 | Namespaced, kalıcı journey kimliği |
| `version` | 1 | Journey'ye özgü SemVer |
| `purpose` | 1 | Kullanıcı açısından ifade edilmiş iş amacı |
| `entry_conditions` | 1..* | Başlatma koşulları |
| `stages` | 1..* | Mantıksal aşamalar |
| `transitions` | 1..* | Kaynak, hedef, guard ve izinli command |
| `required_information` | 0..* | Başarı için gerçekten gerekli slot'lar |
| `allowed_commands` | 1..* | Journey kapsamındaki command allowlist'i |
| `success_conditions` | 1..* | Ölçülebilir terminal koşullar |
| `failure_conditions` | 0..* | Güvenli başarısızlık koşulları |
| `interruption_policy` | 1 | Konu değişimi davranışı |
| `repair_policy_ref` | 1 | Ortak veya journey'ye özel repair sözleşmesi |
| `evidence_policy_ref` | 1 | Factual eylemler için evidence sözleşmesi |

### 13.4 `JourneyInstance`

| Alan | Çokluk | Açıklama |
|---|---:|---|
| `id` | 1 | Konuşmaya özgü instance |
| `definition_id` | 1 | Journey tanımı |
| `definition_version` | 1 | Pinlenmiş tanım sürümü |
| `status` | 1 | Runtime durumu |
| `current_stage` | 1 | Etkin mantıksal aşama |
| `bound_goal_ids` | 1..* | Hizmet edilen goal'lar |
| `slots` | 0..* | Journey'ye özgü değerler |
| `parent_instance_id` | 0..1 | İç içe journey ilişkisi |
| `suspension_reason` | 0..1 | Askıya alınma gerekçesi |
| `started_at` | 1 | Başlangıç |
| `completed_at` | 0..1 | Terminal ise zaman |

Journey'ler duraklatılabilir, iç içe yürüyebilir, geri alınabilir ve kullanıcı tarafından bırakılabilir. Kullanıcı yeni konuya geçtiğinde runtime journey'nin zorunlu cümlesini üretmeye devam edemez.

Framework'ler domain journey'leri ekleyebilir. Çekirdek olarak en az `orientation`, `clarification`, `repair`, `handoff` ve `closing` davranış sınıfları temsil edilebilir olmalıdır.

## 14. Evidence, claim ve epistemik durum

### 14.1 `Claim`

Claim atomik olmalıdır: bağımsız biçimde desteklenebilen veya çürütülebilen tek önerme.

| Alan | Çokluk | Kural |
|---|---:|---|
| `id` | 1 | Kalıcı claim kimliği |
| `version` | 1 | Claim sürümü |
| `subject_ref` | 1 | Önerme öznesi |
| `predicate` | 1 | Namespaced ilişki/özellik |
| `object` | 0..1 | Typed değer veya referent |
| `polarity` | 1 | `POSITIVE`, `NEGATIVE`, `UNKNOWN` |
| `scope` | 0..1 | Claim'in geçerli olduğu kapsam |
| `conditions` | 0..* | Önkoşul ve niteleyiciler |
| `valid_from` | 1 | Geçerlilik başlangıcı |
| `valid_until` | 0..1 | Geçerlilik sonu |
| `criticality` | 1 | Claim risk sınıfı |
| `epistemic_status` | 1 | Mevcut kanıt sonucu |
| `evidence_links` | 1..* | Claim ile evidence ilişkileri |
| `approved_literal` | 0..1 | Literal gerçekleştirme zorunluysa onaylı metin |

### 14.2 `ClaimPolarity` enum'u

- `POSITIVE`: İlişkinin veya değerin var olduğu önerilir.
- `NEGATIVE`: İlişkinin veya değerin olmadığı önerilir.
- `UNKNOWN`: Otorite bu değerin bilinmediğini açıkça ifade eder.

Polarity ile epistemik durum karıştırılamaz. `NEGATIVE/SUPPORTED`, destekli bir olumsuz claim'dir; `POSITIVE/REFUTED`, olumlu önermenin kanıtça çürütüldüğünü gösterir.

### 14.3 `EpistemicStatus` enum'u

| Değer | Anlam |
|---|---|
| `SUPPORTED` | Geçerli ve yeterli yetkili evidence claim'i destekliyor |
| `REFUTED` | Geçerli ve yeterli yetkili evidence claim'i çürütüyor |
| `EXPLICITLY_UNKNOWN` | Yetkili kaynak değerin bilinmediğini veya bulunmadığını söylüyor |
| `CONFLICTED` | Geçerli kaynaklar uyuşmuyor; çözüm bekleniyor |
| `STALE` | Evidence geçerlilik süresi veya güncellik politikası dışında |
| `UNVERIFIED` | Claim mevcut fakat yeterli doğrulama yok |
| `OUT_OF_SCOPE` | Yetkili bilgi kapsamının dışında |

### 14.4 `EvidenceRecord`

| Alan | Çokluk | Kural |
|---|---:|---|
| `id` | 1 | Evidence kimliği |
| `version` | 1 | Evidence sürümü |
| `source_type` | 1 | Kaynak türü |
| `authority_level` | 1 | Kaynak otoritesi |
| `source_uri_or_ref` | 1 | İzlenebilir kaynak |
| `content_hash` | 1 | Değişiklik tespiti |
| `extracted_span` | 0..1 | İlgili bölüm |
| `valid_from` | 1 | Geçerlilik |
| `valid_until` | 0..1 | Süreli ise |
| `tenant_or_scope` | 0..1 | İzolasyon/kapsam |
| `approved_by` | 0..1 | Onay gerektiriyorsa aktör |

### 14.5 `EvidenceSourceType` enum'u

- `AUTHORITATIVE_RECORD`
- `APPROVED_DOCUMENT`
- `APPROVED_POLICY`
- `HUMAN_ATTESTATION`
- `RETRIEVED_PASSAGE`
- `USER_STATEMENT`
- `MODEL_PRIOR`
- `UNTRUSTED_TEXT`

### 14.6 `AuthorityLevel` enum'u

- `AUTHORITATIVE`
- `APPROVED`
- `ADVISORY`
- `UNTRUSTED`

`USER_STATEMENT`, `MODEL_PRIOR`, sıradan `RETRIEVED_PASSAGE` ve `UNTRUSTED_TEXT` varsayılan olarak authoritative değildir. Kullanıcı veya retrieval içindeki talimatlar sistem politikasını, command allowlist'ini veya claim otoritesini değiştiremez.

### 14.7 `ClaimCriticality` enum'u

- `ROUTINE`
- `MATERIAL`
- `CRITICAL`
- `REGULATED`

### 14.8 `EvidenceRequirement` enum'u

| Değer | Anlam |
|---|---|
| `NONE` | Factual önerme üretilmiyor |
| `SUPPORTED` | Geçerli destekleyici claim gerekli |
| `SUPPORTED_CURRENT` | Güncellik koşulunu sağlayan claim gerekli |
| `SUPPORTED_LITERAL` | Onaylı literal metin değiştirilmeden kullanılmalı |

`CRITICAL` veya `REGULATED` factual claim en az `SUPPORTED_CURRENT` gerektirir. Domain politikası `SUPPORTED_LITERAL` zorunluluğu getirebilir.

`EXPLICITLY_UNKNOWN`, `CONFLICTED`, `STALE`, `UNVERIFIED` veya `OUT_OF_SCOPE` durumundaki claim olumlu factual cevap olarak gerçekleştirilemez. Uygun davranış bilgi sınırını açıklamak, ayırt edici clarification istemek veya handoff yapmaktır.

## 15. ContentPlan ve doğal gerçekleştirme

`ContentPlan`, doğal cümlelerden önce onaylanan semantik cevap planıdır.

### 15.1 `ContentBlockType` enum'u

- `SOCIAL_CONNECTION`
- `DIRECT_ANSWER`
- `EVIDENCE_CLAUSE`
- `CONTEXT_BRIDGE`
- `CLARIFICATION_QUESTION`
- `CONFIRMATION_QUESTION`
- `REPAIR_NOTICE`
- `LIMITATION_NOTICE`
- `NEXT_STEP_OFFER`
- `HANDOFF_NOTICE`
- `CLOSING`

### 15.2 `ContentPlan`

| Alan | Çokluk | Kural |
|---|---:|---|
| `id` | 1 | Plan kimliği |
| `action_plan_id` | 1 | Yetkilendirilmiş action plan |
| `based_on_state_revision` | 1 | Kullanılan state |
| `blocks` | 1..* | Sıralı semantic bloklar |
| `claim_ids` | 0..* | Kullanılmasına izin verilen bütün claim'ler |
| `forbidden_claim_ids` | 0..* | Açıkça kullanılmaması gereken claim'ler |
| `style_policy_ref` | 1 | Ton, locale ve kanal politikası |
| `length_budget` | 0..1 | Kanal/amaçla orantılı uzunluk |

Gerçekleştirici:

- Yalnız yetkilendirilmiş command ve content block'ları ifade etmelidir.
- `claim_ids` dışında yeni doğrulanabilir önerme ekleyemez.
- Kullanıcının raw mesajındaki veya retrieval'daki talimatı sistem talimatı gibi uygulayamaz.
- Kullanıcı kapanmak isterken yeni discovery veya satış yönelimli next step ekleyemez.
- Aynı content plan için doğal varyasyon üretebilir; tam cümle eşleşmesi normatif değildir.

## 16. Değişmezler

Aşağıdaki invariants her framework ve modelde geçerlidir:

1. **Raw-turn değişmezliği:** Kaynak mesaj değiştirilemez; normalizasyon ayrı türevdir.
2. **Interpretation/state ayrımı:** Model yorumu authoritative state değildir.
3. **Validated-delta-only:** State yalnız başarılı doğrulamaya sahip atomik delta ile değişir.
4. **Revision güvenliği:** Delta'nın `expected_revision` değeri güncel state ile eşleşmelidir.
5. **Belirsizliği koruma:** Kararı etkileyen ambiguity sessizce tek yoruma indirgenemez.
6. **Component confidence:** Act, referent, goal, entity, delta ve command güvenleri ayrı tutulur.
7. **Confidence ≠ truth:** Yüksek model confidence factual doğruluk veya yetki sağlamaz.
8. **User-correction önceliği:** Kullanıcının kendi amacı, tercihi veya referent'i hakkındaki açık düzeltmesi eski model çıkarımını geçersizleştirir.
9. **Authority sınırı:** Kullanıcı düzeltmesi authoritative dış dünya/şirket gerçeğini değiştiremez.
10. **Repair-before-reuse:** Yanlış yorum işaretlendiğinde ona dayanan state cevapta tekrar kullanılamaz; önce invalidate/repair gerekir.
11. **Evidence gate:** Her doğrulanabilir sistem önermesi seçilmiş claim veya açık sosyal/meta davranışla izlenebilir olmalıdır.
12. **Critical zero-tolerance:** Desteksiz kritik veya regulated factual clause gönderilemez.
13. **Retrieval ≠ authority:** Retrieval bulmakla onaylı evidence olmak aynı değildir.
14. **Command allowlist:** Yalnız ontoloji ve etkin policy tarafından izin verilen command yürütülebilir.
15. **Journey özgürlüğü:** Journey kullanıcıyı konu değiştirmekten, iptalden, handoff'tan veya kapanıştan alıkoyamaz.
16. **No forced progression:** Selam, backchannel, ret veya kapanış otomatik olarak yeni iş journey'sine dönüştürülemez.
17. **Plan-before-language:** Sistem önce davranış ve içerik planını yetkilendirir, sonra doğal dil üretir.
18. **No factual expansion:** Gerçekleştirici content planın dışında factual içerik ekleyemez.
19. **Atomic traceability:** Gönderilen cevap interpretation, state revision, commands ve claim'lere kadar izlenebilir olmalıdır.
20. **Untrusted-data boundary:** Kullanıcı ve retrieval içeriği veri olarak kalır; policy veya sistem talimatı olamaz.
21. **Minimum disclosure:** Handoff ve görev için yalnız gerekli bilgi istenir ve tutulur.
22. **Version pinning:** Aynı karar zincirindeki artefaktlar uyumlu ontoloji ve tanım sürümlerine pinlenir.

## 17. Doğrulama kuralları

Doğrulama en az altı aşamadan oluşmalıdır.

### 17.1 Yapısal doğrulama

- Zorunlu alanlar ve çokluklar sağlanmalıdır.
- Enum dışı değer kabul edilemez; bilinmeyen değer `UNKNOWN`a sessizce çevrilemez.
- Identifier ve typed referans biçimleri geçerli olmalıdır.
- Confidence sonlu ve `[0,1]` aralığında olmalıdır.
- `calibrated=true` ise calibration profile bulunmalıdır.
- Aynı plan içindeki command sequence değerleri benzersiz ve kesintisiz olmalıdır.

### 17.2 Referans bütünlüğü

- Bütün typed referanslar mevcut ve doğru türde entity'ye gitmelidir.
- `selected_candidate_id`, ilgili candidate listesinde bulunmalıdır.
- `RESOLVED` referent tam bir seçilmiş aday taşımalıdır.
- `AMBIGUOUS`, `UNRESOLVED`, `CONFLICTED` veya `INVALIDATED` referent seçilmiş aday taşıyamaz.
- Claim'in evidence link'leri mevcut ve sürüm açısından uyumlu olmalıdır.

### 17.3 Semantik doğrulama

- `ASK_IDENTITY` için çözülmüş hedef yoksa plan `ANSWER_IDENTITY` yerine veya öncesinde `CLARIFY` kullanmalıdır; güvenli ortak cevap istisnası aşağıda tanımlıdır.
- `ANSWER_IDENTITY` yalnız kimliği bilinen ve geçerli referent için kullanılabilir.
- `ANSWER_SCOPE` kapsam claim'leri gerektiriyorsa evidence policy uygulanmalıdır.
- `ANSWER_WITH_EVIDENCE` en az bir uygun claim ve onun geçerli evidence zincirini taşımalıdır.
- `REPAIR` bir invalidation/correction delta'sı veya açık “state değişmeyecek” gerekçesi taşımalıdır.
- Kullanıcı correction'ı varsa eski değere dayanan command aynı plan içinde correction'dan önce veya sonra yeniden kullanılamaz.
- `CLOSE`, açıkça eşzamanlı yeni amaç yoksa discovery veya yeni journey başlatamaz.
- `BACKCHANNEL` tek başına yeni factual cevap veya satış ilerletmesi gerektirmez.

**Güvenli ortak cevap istisnası:** Birden fazla referent/goal adayının tamamında aynı, düşük riskli ve evidence-uyumlu davranış doğruysa runtime bu ortak davranışı clarification olmadan seçebilir. Bu istisna adaylardan yalnız birine uyan factual ayrıntı eklemeye izin vermez.

### 17.4 State transition doğrulaması

- Delta `expected_revision` ile güncel revision eşleşmelidir.
- Her operation precondition'ı uygulanmadan önce doğrulanmalıdır.
- Aynı delta içindeki operation'lar birbiriyle çelişemez.
- `REPLACE` ve `REMOVE` hedefi mevcut olmalıdır.
- `PUSH/POP` yalnız focus stack gibi sıralı hedeflerde kullanılabilir.
- Terminal journey `RESUME` edilemez; yeni instance gerekir.
- `COMPLETED`, `ABORTED` veya `FAILED` journey'nin terminal kayıtları değiştirilemez; düzeltme yeni event ile yapılır.
- Başarısız operation bütün delta'yı reddetmelidir.

### 17.5 Evidence doğrulaması

- Her factual clause atomik proposition'lara ayrılmalıdır.
- Her proposition izinli claim ile entail edilmelidir.
- Claim'in scope ve conditions alanları mevcut context ile uyumlu olmalıdır.
- Zaman duyarlı claim güncellik koşulunu sağlamalıdır.
- `CONFLICTED`, `STALE` veya `UNVERIFIED` claim factual sonuç olarak seçilemez.
- Negative ve unknown claim'ler pozitif dilde gerçekleştirilemez.
- `SUPPORTED_LITERAL` gereksiniminde approved literal değiştirilemez; yalnız izinli biçimsel çevreleme eklenebilir.

### 17.6 Politika, güvenlik ve gerçekleştirme doğrulaması

- Command etkin journey/policy allowlist'inde olmalıdır.
- Tool veya dış etki command'ı gerekli capability ve yetkiyi taşımalıdır.
- Kullanıcı veya retrieval metninden gelen instruction policy olarak uygulanmamalıdır.
- Cevap aktif referent, goal, journey ve interaction mode ile tutarlı olmalıdır.
- Cevap, kullanıcının son correction'ıyla geçersizleşmiş state'i kullanmamalıdır.
- Content plan dışı factual clause bulunmamalıdır.
- Gereksiz clarification, gereksiz handoff, gereksiz satış baskısı ve konu sapması policy metrikleriyle denetlenmelidir.

### 17.7 `ValidationCode` enum'u

- `VALID`
- `SCHEMA_INVALID`
- `ENUM_UNKNOWN`
- `REFERENCE_MISSING`
- `REFERENCE_TYPE_MISMATCH`
- `CONFIDENCE_INVALID`
- `VERSION_MISMATCH`
- `STATE_REVISION_CONFLICT`
- `DELTA_CONFLICT`
- `PRECONDITION_FAILED`
- `AMBIGUOUS_REFERENT`
- `GOAL_UNRESOLVED`
- `COMMAND_NOT_ALLOWED`
- `COMMAND_ORDER_INVALID`
- `JOURNEY_TRANSITION_INVALID`
- `EVIDENCE_REQUIRED`
- `EVIDENCE_STALE`
- `EVIDENCE_CONFLICTED`
- `UNSUPPORTED_CLAIM`
- `CLAIM_SCOPE_MISMATCH`
- `POLICY_VIOLATION`
- `STALE_REPAIR_STATE`
- `UNTRUSTED_INSTRUCTION`
- `PRIVACY_MINIMIZATION_FAILED`

Başarısız doğrulama rastgele fallback cümlesi üretmemelidir. Hata sınıfına göre izinli güvenli sonuç `CLARIFY`, `REPAIR`, `HANDOFF`, `CLOSE` veya cevabı tutma olmalıdır.

## 18. Normatif kısa mesaj örnekleri

Bu bölümdeki cümleler response template değildir. Aynı yüzey formunun state'e göre farklı semantik sonuç vermesi gerektiğini gösteren ontoloji örnekleridir.

### 18.1 Bağlamsız `ne`

Önkoşul:

- Konuşma başlangıcı veya güvenilir focus yok.
- Açık soru ve çözülebilir prior response yok.

Beklenen temsil:

```yaml
dialogue_acts:
  - type: ASK_EXPLANATION
    primary: true
    confidence: low_or_medium
referents:
  - kind: DEICTIC_UNKNOWN
    status: UNRESOLVED
goal_hypotheses:
  - type: RESOLVE_AMBIGUITY
    scope: TURN
    status: ACTIVE
ambiguity:
  type: INSUFFICIENT_CONTEXT
action_plan:
  - CLARIFY
```

Normatif sonuç:

- Sistem belirli ürün, bilgi türü veya journey uyduramaz.
- Sistem kısa ve seçenekleri sınırlı tutan bir clarification yapmalıdır.
- Uzun kurum tanıtımı veya zorunlu discovery başlatılmamalıdır.

### 18.2 Factual cevaptan sonra `ne`

Önkoşul:

- Son sistem cevabı tek bir aktif factual claim içeriyor.
- Kullanıcı turu bu cevabın hemen ardından geliyor.

Makul adaylar:

- `REQUEST_REPEAT`
- `ASK_EXPLANATION`
- `EXPRESS_SURPRISE`

Referent `PRIOR_RESPONSE` veya ilgili `CLAIM` olabilir. Runtime adayları korumalıdır.

Politika:

- Referent ve amaç yeterince yüksek confidence ile çözülüyorsa kısa acknowledge ve aynı geçerli evidence ile tekrar/açıklama yapılabilir.
- “Tekrar” ile “gerekçe” ayrımı cevabın içeriğini maddi biçimde değiştiriyorsa `CLARIFY` kullanılmalıdır.
- Önceki claim stale veya geçersizleşmişse tekrar edilmemeli; evidence yeniden değerlendirilmelidir.

### 18.3 İki seçenekten sonra `ne`

Önkoşul:

- Son sistem turu birden fazla seçenek sundu.

Beklenen ambiguity `REFERENTIAL` veya `SCOPE` olabilir. Sistem seçeneklerden birini seçilmiş sayamaz. Uygun command, seçenekleri sadeleştiren `CLARIFY` veya `ORIENT`tir.

Bu örnek, `ne` için tek bir sabit intent veya cevap bulunmadığını normatif olarak gösterir.

### 18.4 Bağlamsız `sen ne`

Önkoşul:

- Açık karşılaştırma, öneri veya bekleyen soru yok.
- `sen` konuşma ajanına yönelmiş durumda.

Beklenen temsil:

```yaml
dialogue_acts:
  - type: ASK_IDENTITY
    confidence: medium
    mutual_exclusion_group: sen_ne_meaning
  - type: ASK_CAPABILITY
    confidence: medium
    mutual_exclusion_group: sen_ne_meaning
referents:
  - kind: ASSISTANT
    status: RESOLVED
goal_hypotheses:
  - type: ORIENT_TO_ASSISTANT
    status: HYPOTHESIZED
  - type: UNDERSTAND_SCOPE
    status: HYPOTHESIZED
ambiguity:
  type: GOAL
action_plan:
  - ORIENT
  - ANSWER_IDENTITY
  - ANSWER_SCOPE
```

Normatif sonuç:

- `sen` referenti yeterli kanıt varsa `ASSISTANT` olarak çözülebilir.
- Kimlik ve yetenek amaçları birbirine alternatif olarak korunmalıdır.
- Her iki amacı da düşük riskle kapsayan kısa ortak bir yönelim cevabı verilebilir.
- Cevap, company veya system hakkında kanıtsız yetkinlik ekleyemez.
- Bu davranış `sen ne` string'ine bağlı sabit cevap değil, act + referent + goal bileşimidir.

### 18.5 Karşılaştırma bağlamında `sen ne önerirsin`

Önkoşul:

- İki veya daha fazla seçenek focus stack'te.

Beklenen temsil:

- Referent: `ASSISTANT/RESOLVED`
- Primary act: `ASK_RECOMMENDATION`
- Goal: `SEEK_RECOMMENDATION` veya `COMPARE_OPTIONS`
- Command: evidence yeterliyse `ANSWER_WITH_EVIDENCE`; tercih ölçütü eksikse `CLARIFY`

`sen ne` öneki nedeniyle `ANSWER_IDENTITY` seçmek semantik validasyon hatasıdır.

### 18.6 `yok o değil`

Önkoşul:

- Önceki turda bir referent model tarafından çözülmüş.

Beklenen davranış:

1. `REJECT + CORRECT` act'leri çıkarılır.
2. Eski referent `INVALIDATED` yapılır.
3. Ona bağlı goal/slot/journey state'i yeniden değerlendirilir.
4. Yeni referent çözülemiyorsa `REPAIR + CLARIFY` planlanır.
5. Eski referent'e dayalı factual cevap yasaktır.

## 19. Sürümleme ve genişletme

### 19.1 SemVer kuralları

Ontoloji `MAJOR.MINOR.PATCH` kullanır.

- **MAJOR:** Mevcut verinin anlamını veya validasyon sonucunu geriye uyumsuz değiştiren güncelleme; enum anlamı değişikliği, zorunlu alan ekleme, invariant kaldırma/değiştirme.
- **MINOR:** Geriye uyumlu yeni enum değeri, isteğe bağlı alan, yeni validation code veya yeni extension point.
- **PATCH:** Semantiği değiştirmeyen açıklama, yazım, örnek veya referans düzeltmesi.

### 19.2 Kimlik ve enum yaşam döngüsü

- Yayınlanmış enum değerinin anlamı değiştirilemez.
- Kaldırılacak değer önce `deprecated` olarak işaretlenir; kimliği başka anlam için yeniden kullanılamaz.
- Domain genişletmeleri namespaced olmalıdır.
- Bilinmeyen enum değeri sessizce `UNKNOWN`a dönüştürülemez; version mismatch olarak quarantine veya migration gerekir.
- Claim, journey ve policy kimlikleri sürümler arasında yeniden kullanılabilir; semantik değişim kendi version artışını gerektirir.

### 19.3 Runtime sürüm pinleme

Her `TurnInterpretation`, `StateDelta`, `DialogueState`, `ActionPlan` ve `ValidationResult` `ontology_version` taşımak ZORUNDADIR.

Bir conversation state yeni MAJOR sürüme otomatik geçirilemez. Migration:

1. Kaynak ve hedef sürümü açıkça belirtmelidir.
2. State, goal, journey, claim ve event referanslarını korumalıdır.
3. Geri döndürülemez dönüşümleri raporlamalıdır.
4. Aynı altın senaryolar üzerinde semantic regression doğrulaması yapmalıdır.

Golden test artefaktları ontoloji, journey, claim ve policy sürümlerine pinlenmelidir. Sürüm değişikliği test beklentilerini sessizce güncelleyemez.

### 19.4 Extension point'ler

Aşağıdakiler uygulama/domain tarafından genişletilebilir:

- Entity type ve slot tanımları
- Goal `predicate` değerleri
- Journey tanımları ve aşamaları
- Claim predicate'leri
- Evidence ve risk politikaları
- Style/locale politikaları
- Capability ve tool tanımları

Çekirdek invariants, trust boundary, evidence gate ve validated-delta-only kuralları domain extension ile zayıflatılamaz.

## 20. Ana araştırma belgesine eşleme

| Bu ontoloji | Ana araştırma bölümü | İlişki |
|---|---|---|
| Bölüm 2–3: kapsam ve bilgi zinciri | §1, §2, §4 | “Tek büyük prompt” yerine kontrollü hibrit ayrımın normatif karşılığı |
| Bölüm 4–8: interpretation, act, referent, entity, goal | §3, §4.1, §4.3, §9.1 | Tek intent yerine bileşimsel anlam sözleşmesi |
| Bölüm 9 ve 11: DialogueState ve StateDelta | §4.2, §6.1, §6.4 | Ortak zemin, focus, uncertainty ve repair'in revision'lı state modeli |
| Bölüm 10: confidence/ambiguity | §3.1–3.4, §4.1, §6.4, §7 | Model confidence'ın otorite olmaması ve belirsizliği koruma |
| Bölüm 12: Command ve ActionPlan | §4.4, §5.1, §7 | LLM'in doğrudan cevap yerine izinli komut önermesi |
| Bölüm 13: Journey | §4.5, §5, §10 | Flow'un cümle sırası değil iş amacı ve geçiş sözleşmesi olması |
| Bölüm 14: Claim/evidence | §4.6, §6.5, §8 | Company graph otoritesi, RAG sınırı ve epistemik durum |
| Bölüm 15: ContentPlan | §4.7–4.8 | İçerik seçimi ile doğal Türkçe gerçekleştirmeyi ayırma |
| Bölüm 16–17: invariants/validation | §4.8, §8, §9, §11 | Trusted runtime, hard gate, güvenlik ve altın test doğrulaması |
| Bölüm 18: `ne`, `sen ne` ve repair örnekleri | §3.2–3.4, §9.2 | Kısa mesajların yüzey kuralı olmadan state'e bağlı yorumlanması |
| Bölüm 19: versioning | §9–11 | Framework ve model karşılaştırmalarında sabit davranış sözleşmesi |

## 21. Uyum kriteri

Bir sistem bu ontolojiye uyumlu sayılmak için en az şunları göstermelidir:

1. Turn interpretation'ı dialogue act, referent, goal hypothesis, ambiguity ve confidence bileşenleriyle üretebilmek.
2. Authoritative state'i yalnız doğrulanmış ve revision-korumalı delta ile değiştirmek.
3. Bir tur için sıralı birden fazla command planlayabilmek.
4. Journey'leri duraklatma, değiştirme, repair, handoff ve closing ile güvenli biçimde yönetebilmek.
5. Factual cevapları atomik claim ve geçerli evidence'e bağlamak.
6. Belirsiz referent ve amaçlarda uydurmak yerine güvenli ortak davranış veya clarification seçmek.
7. Content plan dışı factual üretimi engellemek.
8. Her gönderilen cevabın interpretation, state revision, command ve claim trace'ini tutmak.
9. `ne` ve `sen ne` gibi kısa mesajların yorumunu yalnız yüzey formuna değil context ve state'e göre değiştirebilmek.
10. Ontoloji, journey, claim ve policy sürümlerini pinlemek ve migration'ı açıkça yönetmek.

Bu kriterlerin herhangi birini yalnızca system prompt talimatıyla sağlamak yeterli değildir; davranış trusted doğrulama ve izlenebilir runtime kaydıyla gösterilmelidir.
