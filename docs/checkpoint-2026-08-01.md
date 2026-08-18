# Ashira AI — Checkpoint (2026-08-01)
> Yeni bir mühendislik context'i bu dosyadan başlayabilir.
> Bu dosyada parola, API tokenı, webhook verify tokenı, özel anahtar veya
> müşteri mesajı bulunmaz ve bulunmamalıdır.

## Bu checkpoint'in sonucu

Şirketi sabit bir sektör veya ürün formu olarak değil, versioned typed graph
olarak temsil eden ve yönetici konuşmasıyla güvenli şekilde kurabilen temel
tamamlandı. Yönetici ham JSON yazmaz; LLM de JSON'a doğrudan yazamaz.

Test edilen, dürüst matematiksel iddia şudur:

\[
\mathrm{Reach}(F_{\Sigma,R}) = \mathcal C_{\Sigma,R}
\]

- \(\Sigma\): kapalı `CompanyAgentConfig` çekirdek şeması.
- \(R\): ID/sürüm eşleşmesi ve doğrulayıcısı bulunan kayıtlı sektör modülleri.
- \(\mathcal C_{\Sigma,R}\): bu şema ve bu modüller altında geçerli şirket
  konfigürasyonları.
- \(F_{\Sigma,R}\): kanal-bağımsız soru grafiği ile typed staged reducer.

Bu, “dünyadaki her olası şirketi önceden biliyoruz” iddiası değildir. Yeni bir
sektör kavramı gerekiyorsa, serbest JSON alanı açmak yerine validasyonlu,
namespaced bir modül olarak kaydedilir.

## Temel mimari

```text
admin mesajı / Excel / PDF
        ↓
yerel LLM: yalnızca intent veya dar proposal
        ↓
sunucu: aktif akış adımını ve görünür etkileşimi belirler
        ↓
immutable preview → yönetici açıkça kabul eder
        ↓
typed staged graph → materialize → CompanyAgentConfig
```

LLM hiçbir zaman akış adımı seçemez, koleksiyon kapatamaz, lifecycle
onaylayamaz veya taslağa JSON Patch yazamaz. Dosyalar da talimat değil
kanıttır: önce proposal/preview, sonra açık kabul gerekir.

Müşteri tarafı için ayrı ve daha sıkı ilke geçerlidir:

```text
company JSON = bilgi otoritesi
yerel LLM    = intent + fact-ID önerisi
güvenilir kod = fact doğrulama + literal customer_text render
```

Şirket bilgilerini LoRA/fine-tuning ile ağırlıklara gömmek kapsam dışıdır.
Değişken fiyat, stok, politika ve ürün bilgileri JSON/retrieval katmanında
kalır; güncelleme anında etkili ve denetlenebilir olur.

## Uygulanan evrensel akış

### Şema toplamlığı

`config_flow.py`, `CompanyAgentConfig` çekirdeğinden semantic slot manifestini
türetir. Çekirdekte şu anda **57** düzenlenebilir semantic slot, **18**
`FlowSpec` ailesi vardır.

İki bağımsız invariant zorunludur:

1. Her core slotun tam olarak bir `FlowSpec` sahibi vardır.
2. Her core `FlowSpec` için typed reducer kayıtlıdır.

Böylece “soru sorabiliyoruz ama geçerli konfigürasyon kuramıyoruz” türü
questionnaire-only boşluğu kapatılmıştır.

Kapsanan yapısal aileler: kuruluş/agent politikası, parties, products/services,
ilişkiler, facts, müşteri profilleri ve nested alanları, süreçler ve
transition'ları, politikalar ve koşulları, referans bağları, lifecycle review
ve kayıtlı modül konfigürasyonu.

### Yetkili staged reducer

`configuration_session.py` geçici fakat typed bir `StagedCompanyDraft`
tutar. Geçici olarak eksik bağlar tutulabilir: örneğin fact henüz subject
seçilmeden, offering provider seçilmeden veya policy template fact seçilmeden
toplanabilir. `materialize()` ancak tüm gerekli referanslar bağlandığında,
açık collection scope'ları kapandığında ve şema/modül doğrulaması geçtiğinde
nihai `CompanyAgentConfig` üretir.

Her kabul aksiyonu sunucu tarafında üretilen, HMAC-bağlı opaque bir referanstır:

```text
tenant_id + admin_id + session_id + revision + FlowSpec.id
+ command/proposal + payload digest + scope + expiry
```

Bu nedenle preview inerttir; forged, stale, cross-tenant veya cross-admin
aksiyon mutation yapamaz. Aynı kabul aksiyonunun tekrar gelmesi no-op'tur.

### Konuşma UX kuralı

Koleksiyonlar açıkça kapatılmadan bot uzak bir konuya atlayamaz. Örneğin ürün
eklendiğinde bot “başka ürün/hizmet/variant var mı?”, “Excel/PDF yüklemek ister
misiniz?” veya “liste tamam mı?” yakınlığında kalır. Sessizlik “tamam” anlamına
gelmez. Aynı kural customer-profile fields, process transitions ve policy
conditions için de geçerlidir.

Niyet tabanlı UI şeması:

```text
validated intent → session-derived InteractionGoal → InteractionPlan
                 → WhatsApp/web renderer
```

Renderer aynı semantiği WhatsApp reply buttons, list, Flow, document prompt
veya metin fallback olarak gösterebilir. Etiketler/istemci payload'ları yetki
değildir; sadece server-issued action ref kabul edilir.

## İlgili dosyalar

- `apps/api/src/modules/agents/company_config.py` — canonical şirket grafiği.
- `apps/api/src/modules/agents/config_flow.py` — semantic manifest ve
  kanal-bağımsız `FlowSpec` derleyicisi.
- `apps/api/src/modules/agents/configuration_session.py` — typed command,
  preview, acceptance ledger, staged aggregate ve materializer.
- `apps/api/src/modules/agents/company_runtime.py` — müşteri runtime sınırı;
  henüz webhook'a bağlı değildir.
- `docs/universal-configuration-flow.md` — formal kapsam/kanıt sınırı.
- `docs/admin-configuration-conversation.md` — yönetici konuşma ilkeleri.
- `docs/intent-driven-interactions.md` — niyet → surface eşlemesi.
- `experiments/universal_config_flow_e2e.py` — constructive universal test.

## Son doğrulama (2026-07-31)

Tüm aşağıdaki denetimler geçti:

| Denetim | Sonuç |
|---|---:|
| Universal configuration constructive E2E | 14/14 |
| `test_config_flow.py` + `test_configuration_session.py` | 17/17 |
| Guided admin configuration E2E | 16/16 |
| Intent-driven interaction policy E2E | 24/24 |
| İlgili Python dosyalarının `py_compile` kontrolü | geçti |
| `git diff --check` | temiz |

Universal E2E; core-slot totality, tek-owner kuralı, typed reducer totality,
collection gates, topolojik referans bağımlılıkları, katalogsuz klinik,
marketplace, kayıtlı/kayıtsız modül davranışı, iki constructive graph
round-trip ve action/scope güvenlik kurallarını kapsar.

Makinede `pytest` paketi yüklü olmadığı için bu iki test dosyası aynı test
fonksiyonlarını çalıştıran küçük pytest-compatible runner ile kontrol edildi.
CI veya proje dev environment'ında normal `pytest` ve Ruff ayrıca çalıştırılmalı.

## Bilinçli sınırlar / henüz yapılmayanlar

1. `ConfigurationFlowSession` şu anda in-memory referans implementasyonudur.
   Production için staged aggregate ve action ledger atomik olarak DB'de
   saklanmalıdır.
2. Eski doğrudan `PATCH .../draft` yolu, aktif configuration session varken
   paralel LLM-yazma yolu olmamalıdır. Sadece auditli override/migration yolu
   olmalı veya aktif session'ı geçersiz kılmalıdır.
3. Legacy admin builder henüz canonical `company_config` session'ına migrate
   edilmedi. Yeni UI/WhatsApp renderer bu session'ın action referanslarını
   kullanmalıdır.
4. `company_runtime.py` içindeki geçiş dönemi serbest metin `reply` alanı,
   müşteri-facing strict path için kaldırılıp yalnızca validated fact-ID +
   deterministic renderer düzenine geçirilmelidir.
5. WhatsApp webhook'a otomatik müşteri cevabı bağlanmadı. Önce tenant → live
   agent çözümü, conversation state, fact audit trail ve handoff gerekir.
6. Arch Ollama host ile Windows API arasındaki private WireGuard yolunun kurulumu
   henüz tamamlanmadı. Ollama portu kamuya açılmamalıdır.

## Sonraki güvenli iş sırası

1. Yerel model üzerinde synthetic şirket grafikleriyle intent/fact-ID golden
   evaluation'ı genişletmek.
2. `ConfigurationFlowSession` için kalıcı DB adapter'ı ve atomic action
   tüketimini eklemek.
3. Legacy builder'ı typed session + preview + explicit acceptance katmanına
   taşımak.
4. Aynı `InteractionPlan`ı önce WhatsApp-benzeri web arayüzünde render etmek;
   gerçek WhatsApp entegrasyonunu bundan sonra eklemek.
5. Strict customer runtime'ı fact-ID-only hale getirip synthetic inbound
   webhook E2E'si ile doğrulamak.

## Güvenlik / çalışma ağacı notu

Repository bilinçli olarak dirty. Başka servisleri, Windows/IIS sitelerini,
DNS'i, Meta ayarlarını veya mevcut web değişikliklerini resetlemeyin ve
değiştirmeyin. Hiçbir credential'ı `.md`, Git, terminal geçmişi veya chat'e
yazmayın. Ham müşteri mesajları cloud LLM'e gönderilmez; yerel model sınırında
kalır.
