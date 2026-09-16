# ADR-003: Self-Servis Bilgi Kaynakları (Doküman / Web Sitesi / Görsel) ve Hibrit Cevap Modu

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-15
- **Kapsam:** Tenant'ın kendi bilgi tabanını yüklemesi ve tarattırması; aday fact/ürün/görsel üretimi; otomatik + geri alınabilir yayın; korumalı konular literal kalırken açıklayıcı cevapların modelce yazılması; admin chat ve panel yüzeyleri
- **İlgili:** [ADR-002](ADR-002-falkordb-graphrag-retrieval.md) (retrieval), [Vector retrieval mimarisi](../multi-tenant-vector-retrieval-architecture.md) §7.3/§8 (Knowledge Ops), [Admin konfigürasyon konuşması](../admin-configuration-conversation.md) (dosya = kanıt), [Diyalog ontolojisi](../dialogue-behavior-ontology-v1.md) §14.8 (`EvidenceRequirement`)

## 1. Karar

### 1.1 Bilgi kaynakları (Knowledge Ops, otomatik + geri alınabilir)

Her tenant, asistanı için **kendi web sitesini** kaydeder veya **PDF / XLSX / CSV / Markdown / metin** yükler. Sistem:

1. **Yerel çıkarım** (pypdf, openpyxl, trafilatura + BeautifulSoup; ham dosya buluta gitmez) → sayfa/tablo başına `knowledge_snapshots` (locator: `dosya.pdf#page=3`, `dosya.xlsx#sheet=Fiyat&rows=2-41`, sayfa URL'si; `content_hash` ile değişmeyen içerik yeniden işlenmez).
2. **Chunk'lama** (`knowledge_chunks`, 1400 karakter / 150 örtüşme) → **BGE-M3 embedding** → tenant başına **`kn_<tenant>` FalkorDB grafiği**: `(:Chunk)-[:MENTIONS]->(:Subject)`, `(:Chunk)-[:FROM]->(:Source)`; runtime indeksi `kb_*` (ADR-002) ayrı kalır.
3. **Mention + aday çıkarımı** (Qwen3 8B, JSON şemasıyla kısıtlı): chunk'ın hangi onaylı ürünlerden bahsettiği, **yeni ürün önerileri** (ad, tür, ebeveyn) ve **aday fact'ler** (özne, kategori, müşteri metni, arama terimleri, chunk'tan **literal kanıt alıntısı**, güven). Sunucu her şeyi yeniden doğrular: bilinmeyen özne düşer, alıntı chunk'ta yoksa düşer, 4 kelimeden kısa "fact" düşer, dosya içi talimatlar yetkisizdir.
4. **Görsel keşfi**: JSON-LD `Product.image`, `og:image`, ürün başlığına yakın `<img>`; logo/ikon/sprite süzülür; Pillow ile doğrulanır, ≥300 px, JPEG/PNG'ye dönüştürülür, ≤5 MB; `knowledge_media`'da saklanır ve **`/media/k/<tenant>/<sha>.<ext>`** üzerinden değişmez public URL ile sunulur (Meta hot-link yapmaz).
5. **Yayınlama** (`KnowledgePublisher`): adaylar mevcut tek mutasyon noktasından (`AgentService.update_draft`) draft'a yazılır; draft publisher'a aitse `promote_to_live` ile LIVE olur ve ADR-002 indeksi yeniden kurulur. Kurallar:
   - `customer_visible = true` yalnızca **tenant'ın kendi kaynağı** + `confidence ≥ KNOWLEDGE_AUTO_PUBLISH_THRESHOLD` + **korumalı olmayan** konu (kategori ∉ {commercial_rule, availability, delivery, eligibility} ve metinde fiyat/stok/teslimat/garanti/sertifika/uygunluk kökleri yok).
   - Korumalı fact'ler draft'a **`customer_visible=false`** olarak girer ("staged"); yalnızca **şirket sahibi** görünür yapabilir.
   - **Yeni ürün adayları asla otomatik değildir**: admin tek tıkla onaylar, ona bağlı fact'ler onayla birlikte yayınlanır.
   - Görsel: ürün henüz görselsizse `whatsapp_presentation.assets` + `offering_media` olarak bağlanır; carousel otomatik oluşturulmaz.
   - Admin'in açık draft'ı varsa yayınlayıcı yalnızca draft'a yazar, canlıya almaz (`deferred_reason`).
6. **Geri alma**: `AgentService.publish_hotfix` LIVE'ın düzeltilmiş kopyasını yeni LIVE sürüm olarak yayınlar (admin draft'ını beklemez); draft da güncellenir; aday `revoked` olur ve aynı içerik tekrar tarama sonrasında **yeniden yayınlanmaz** (fingerprint). Kaynak silme = o kaynağın yayınladığı her şeyi tek hotfix'te kaldırma.

### 1.2 Hibrit cevap modu (`response_mode = "hybrid"`)

Ontolojinin `EvidenceRequirement` merdiveni koda taşındı (`grounded_generation.py`):

| Konu (planlayıcı `topic`) | Gereksinim | Gerçekleştirme |
|---|---|---|
| price, stock, delivery, suitability, warranty, certification, quote, visuals, social, other | `SUPPORTED_LITERAL` | **Literal** onaylı `customer_text` (bugünkü davranış) |
| details (ve tenant izin verirse contact) | `SUPPORTED` | **Üretilmiş**: model, seçilmiş fact'ler + doküman chunk'larına atıf vererek Türkçe yazar |

Kategori merdiveni de uygulanır (commercial_rule/availability/eligibility/delivery/social/other → literal); tenant `literal_fact_ids` ile fact sabitleyebilir; metninde para birimi, ISO/TSE/DIN kodu veya korumalı kök geçen fact **her zaman literal** kalır.

Üretim tek model çağrısıdır: JSON şema `blocks[]` (`DIRECT_ANSWER`, `EVIDENCE_CLAUSE`, `CONTEXT_BRIDGE`, `LIMITATION_NOTICE`, `SOCIAL_CONNECTION`, `CLOSING`), `request_index` enum'u yalnızca üretilebilir istekleri, `claim_refs` enum'u yalnızca sunulan kanıt id'lerini içerir. **Deterministik denetim** (`grounded_audit.py`): kanıtsal bloklardaki her sayı/kod/ölçü/malzeme kodu atıf yapılan kanıtta geçmeli; fact-dışı bloklarda değer olamaz; korumalı sözcükler, para birimi, standart kodları, enjeksiyon işaretleri, onaylı olmayan link → blok düşer; düşük sözcük örtüşmesinde `bge-reranker-v2-m3` cross-encoder eşiği (varsayılan 0,30). Denetimden geçmeyen istek **literal fact'lere döner**; hiçbir hata handoff üretmez. Denetlenmiş bloklar `RuntimeTurn.answer_origin ∈ {literal, generated, mixed}`, `answer_verified`, `evidence_ids`, `generation` (audit izi) ile işaretlenir; `fact_ids` yalnızca fact içerir (chunk id'leri asla).

Doküman kanıtı: `kn_<tenant>` grafiğinden `EvidenceRetriever` (vektör + Türkçe full-text + mention süzgeci, RRF, rerank) ile en fazla `max_evidence_chunks` pasaj; pasajlar prompt'a girmeden **temizlenir** (kontrol karakterleri, enjeksiyon kalıpları, korumalı/para birimi içerik atılır).

## 2. Ölçümler (M5 Max, yerel Qwen3 8B, reranker açık)

| Ölçüm | Değer |
|---|---|
| Doküman ingest (3 chunk, gerçek model) | 16 sn; 8 fact + 1 ürün adayı; korumalı "minimum sipariş" gizli kaldı |
| Hibrit tur (planlayıcı + kanıt + üretim + denetim) | 2,5–6 sn ısınmış; üretim çağrısı 0,7–2,9 sn |
| Denetim örnekleri | "6211 rulman 55 mm mil çapına uygundur." → doğrulandı; "GG-30" uydurma değer → düştü, literal fallback |
| Web sitesi ingest (mock site) | robots.txt'e uyuldu, logo süzüldü, ürün görseli JPEG'e çevrilip LIVE'a bağlandı |

## 3. Güven sınırları

- Ham doküman/sayfa metni hiçbir zaman doğrudan müşteri cevabı değildir; yalnızca (a) yayınlanmış onaylı fact veya (b) denetimden geçmiş, atıflı üretilmiş blok müşteriye ulaşır.
- Tenant/agent kimliği webhook/DB'den; RLS tüm yeni tablolarda; graf adları tenant'a bağlı.
- Yükleme: magic byte + uzantı; 20 MB; yalnızca desteklenen türler. Tarama: aynı host, robots.txt her URL'de fail-closed, hız sınırlı, `localhost`/özel host reddi.
- Meta medya: yalnızca deployment'ın public HTTPS origin'i, JPEG/PNG ≤ 5 MB.
- `HYBRID_GENERATION_ENABLED=false` anahtarı tüm hibrit tenant'ları anında strict davranışa döndürür.

## 4. Kod haritası

```
apps/api/src/modules/knowledge/models.py        6 RLS tablosu (sources, documents, snapshots, chunks, candidates, media)
apps/api/alembic/versions/b7c4d9e2f013_*.py     migration
apps/api/src/modules/knowledge/extract.py       PDF/XLSX/CSV/MD/HTML → TextUnit; html_to_page (trafilatura + bs4 + JSON-LD)
apps/api/src/modules/knowledge/chunking.py      paragraf tabanlı chunk'lama
apps/api/src/modules/knowledge/crawler.py       aynı-host tarayıcı (robots, sitemap, hız sınırı)
apps/api/src/modules/knowledge/media.py         görsel keşfi, Pillow doğrulama/dönüştürme
apps/api/src/modules/knowledge/extraction.py    Qwen3 mention/yeni ürün/aday fact çıkarımı + sunucu doğrulaması
apps/api/src/modules/knowledge/evidence.py      kn_<tenant> grafiği + EvidenceRetriever + ScopedEvidenceRetriever
apps/api/src/modules/knowledge/publisher.py     ConfigPatcher, KnowledgePublisher (auto-publish, staged, hotfix revoke)
apps/api/src/modules/knowledge/ingest.py        KnowledgeIngestService (sync_source)
apps/api/src/modules/knowledge/router.py        /api/v1/knowledge/* + public /media/k/*
apps/api/src/workers/knowledge.py               sync_knowledge_source, delete_knowledge_source (knowledge kuyruğu)
apps/api/src/modules/agents/grounded_types.py   ContentPlan, EvidenceItem, GeneratedAnswer, EntailmentVerifier
apps/api/src/modules/agents/grounded_audit.py   deterministik denetim + cross-encoder gate
apps/api/src/modules/agents/grounded_generation.py  gereksinim tabloları, kanıt derleme, şema/prompt, generate_answers
apps/api/src/modules/agents/semantic_dialogue.py    compose_reply(generated=), reply_to_requests hibrit dalı
apps/api/src/modules/admin_chat/workflow_knowledge.py  "knowledge" ve "knowledge_review" sohbet iş akışları
apps/web/src/components/workspace/knowledge-panel.tsx  Bilgi kaynakları sekmesi (site, dosya, aday, görsel)
```

## 5. Açık işler

- ~~OCR'lı taranmış PDF'ler (yalnızca metin katmanı okunur).~~ **2026-09-16 (ADR-004/WP2):** metin katmanı boş sayfalar pypdfium2 ile rasterize edilip NeMo Retriever page-elements/OCR/table-structure zincirinden geçer; locator `dosya.pdf#page=N&bbox=…`. Ayrıca chunk'lar guardrail'den geçer (WP1) ve görseller vision modeliyle doğrulanır (WP3); alt metin `sanitize_alt_text` ile temizlenir.
- Periyodik yeniden tarama (`sync_policy` kaydedilir; beat girdisi henüz yok, `POST /sources/{id}/sync` ile tetiklenir).
- JavaScript ile render edilen siteler için Playwright fallback (mevcut lead crawler'ından uyarlanabilir).
- Chunk kanıtı için LIMITATION_NOTICE davranışının Türkçe golden set ile ölçülmesi (`experiments/`).
