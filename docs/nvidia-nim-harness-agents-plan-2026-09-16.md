# NVIDIA NIM harness ajanları — uygulama planı (2026-09-16)

Bu plan, `feat/graphrag-self-service-knowledge` dalındaki mimarinin (ADR-002 GraphRAG retrieval,
ADR-003 self-servis bilgi kaynakları + hibrit cevap) üzerine build.nvidia.com kataloğundan seçilen
NIM modellerini **harness ajanları** olarak eklemenin yol haritasıdır. Girdi:
[`nvidia-nim-agents-handoff-2026-09-16.md`](./nvidia-nim-agents-handoff-2026-09-16.md).
Kodlama ajanına verilecek prompt ayrı dosyadadır:
[`nvidia-nim-harness-agents-coding-prompt-2026-09-16.md`](./nvidia-nim-harness-agents-coding-prompt-2026-09-16.md).

"Harness ajanı" burada şu anlama gelir: **karar vermeyen, müşteriye metin yazmayan, yalnızca sınıflandıran /
çıkaran / dönüştüren ve sonucu deterministik sunucu koduna teslim eden** bir model çağrısı. Mevcut
"model seçer, sunucu render eder" sözleşmesi hiçbir iş paketinde gevşemez.

## 0. Özet

| Öncelik | İş paketi | Ne ekler | Nereye dokunur |
|---|---|---|---|
| WP0 | Temel | Ortak NIM HTTP istemcisi, `Settings` şeması, üretimde **veri sınırı kapısı**, GPU compose profili, preflight | `integrations/nim/`, `core/config.py`, `infra/`, `scripts/runtime_preflight.py` |
| WP1 | Guardrail kapısı | Müşteri mesajı (model çağrısından önce) ve chunk/OCR sayfası (ingest) için jailbreak + içerik güvenliği + konu kontrolü; fail-closed | `workers/agent_runtime.py`, `modules/knowledge/ingest.py`, `modules/guardrails/` |
| WP2 | OCR / tablo zinciri | Metin katmanı boş PDF sayfaları için page-elements → OCR → table-structure; locator `dosya.pdf#page=N&bbox=…` | `modules/knowledge/extract.py`, `modules/knowledge/ocr.py`, `ingest.py` |
| WP3 | Görsel doğrulama | `media.py` adaylarının ürün eşleşmesi + alt metin (JSON şema); `KnowledgeMedia.subject_id/score/alt_text/verification` | `modules/knowledge/vision.py`, `ingest.py`, migration |
| WP4 | Embedding + rerank adaptörleri | NIM `/v1/embeddings` (`input_type`), NIM `/v1/ranking`; `kn_` grafiği için yeniden-embed yolu; golden set A/B | `integrations/embeddings.py`, `integrations/reranker.py`, `knowledge/indexer.py`, `knowledge/evidence.py` |
| WP5 | Rol bazlı LLM seçimi | `get_llm_client(role)`; `nvext.guided_json`; müşteri → yerel, çıkarım/hafıza → NIM | `integrations/llm.py`, tüm çağrı noktaları |
| WP6 | İsteğe bağlı | Ses notu → metin (Whisper), onaylı fact çevirisi adayı (Riva translate) | `outreach/webhooks.py`, `workers/agent_runtime.py`, `knowledge/translation.py` |

## 1. Değişmezler (girdi dokümanlarından türetildi, her WP'de aynen geçerli)

1. **Veri sınırı.** Müşteri mesajı, tenant dokümanı/sayfası/görseli/ses notu yalnızca operatörün kendi
   GPU sunucusundaki *Downloadable NIM* konteynerine gider. build.nvidia.com "Free Endpoint"
   (`integrate.api.nvidia.com`) yalnız sentetik veriyle `experiments/` altında kullanılabilir;
   uygulama kodu bu host'a asla istek atmaz. Bu kural **kodda** zorlanır (WP0 `production_runtime_errors()`).
2. **Müşteri metni tek kaynaktan.** Müşteriye ulaşan her cümle ya onaylı `customer_text` ya da
   ADR-003 denetiminden geçmiş, atıflı üretilmiş bloktur. Hiçbir NIM çıktısı doğrudan render edilmez.
3. **Fine-tuning yok.** Hiçbir WP model ağırlığına şirket bilgisi koymaz; her şey JSON + retrieval.
4. **Fail-closed.** Guardrail erişilemezse model çağrılmaz, onaylı güvenli tur döner. OCR/vision/ASR
   erişilemezse ilgili girdi işlenmez (sessizce atlanır, `stats`/`audit`'e yazılır); asla ham
   metin/uydurma sonuç akışa girmez. Retrieval'da olduğu gibi hata handoff üretmez.
5. **Ayar tek yerde.** Her yeni ayar `Settings` + kök `.env.example` + `apps/api/.env.example`
   (iki dosya farklı yorum stiline sahip, ikisi de güncellenir).
6. **Tenant izolasyonu.** Yeni tablo → RLS'li migration (`b7c4d9e2f013` deseni). Bu planda yeni tablo
   yok; sütun eklemeleri mevcut RLS'li tablolara yapılır.
7. **Kalite kapıları.** `ruff check`, `mypy src` (strict), `pytest` yeşil; her WP'nin kabul testi
   (respx ile kaydedilmiş NIM yanıtları, Postgres'li entegrasyon testleri) ve `experiments/nim/`
   altında Türkçe golden set ölçümü + JSON rapor.
8. **Audit gizliliği.** `AgentRuntimeJob.audit`, `KnowledgeChunk.guard`, `KnowledgeMedia.verification`
   yalnızca etiket/skor/model adı/süre içerir; müşteri metni, görsel ve ses baytı içermez.
   İstisnalar ve loglar sağlayıcı yanıt gövdesini taşımaz (mevcut `LLMCompletionError` konvansiyonu).

## 2. Doğrulanan NIM API bulguları ve handoff düzeltmeleri

Aşağıdaki satırlar bu oturumda NVIDIA dokümanlarından doğrulandı; "doğrulanacak" olanlar kodlama
ajanının **konteyneri çalıştırıp `GET /v1/openapi.json` okuyarak** sabitleyeceği ve respx fixture'ına
kaydedeceği noktalardır.

| Rol | Model / imaj | Endpoint ve şekil | Durum |
|---|---|---|---|
| Jailbreak | `nvcr.io/nim/nvidia/nemoguard-jailbreak-detect:1.10.1` | `POST /v1/classify` `{"input": "…"}` → `{"jailbreak": bool, "score": float}`; skor −1..1, pozitif = jailbreak | doğrulandı |
| İçerik güvenliği | `nvcr.io/nim/nvidia/llama-3.1-nemoguard-8b-content-safety:1.10.1` (handoff'taki `nemotron-3.5-content-safety` aynı Aegis taksonomisini kullanır, model kartından teyit edilecek) | `/v1/completions` veya `/v1/chat/completions`; prompt şablonu S1–S23 kategori listesini içerir; yanıt JSON: `"User Safety": "safe|unsafe"`, opsiyonel `"Response Safety"`, `"Safety Categories": "S10, S11"` | şablon doğrulandı, Nemotron 3.5 varyantı doğrulanacak |
| Konu kontrolü | `nvcr.io/nim/nvidia/llama-3.1-nemoguard-8b-topic-control:1.10.1` | `POST /v1/chat/completions`; system prompt izinli konuları anlatır ve şu cümleyle biter: `If any of the above conditions are violated, please respond with "off-topic". Otherwise, respond with "on-topic".`; çıktı tam olarak `on-topic` / `off-topic` | doğrulandı |
| Sayfa düzeni + tablo yapısı | `nvcr.io/nim/nvidia/nemotron-object-detection:2.0` (`nvidia/nemotron-page-elements-v3`, `nvidia/nemotron-table-structure-v1`) | `POST /v1/page-elements`, `POST /v1/table-structure`; istek `{"input":[{"type":"image_url","url":"data:image/jpeg;base64,…"}]}`; yanıt: sınıf + bbox + güven (koordinat biçimi doğrulanacak) | endpoint doğrulandı, yanıt şeması doğrulanacak |
| OCR | `nemotron-ocr-v2` (gerekirse `paddleocr`) | `/v1/infer` veya benzeri; yanıt: metin + bbox + güven | doğrulanacak |
| Embedding | `nvidia/nemotron-3-embed-1b` | `POST /v1/embeddings` `{"model","input":[…],"input_type":"query"|"passage","encoding_format":"float","truncate":"END"}`; yanıt `data[].embedding`. **Düzeltme:** bu model yalnız **native 2048** boyutu destekler (`dimensions` ile kesme yok). Kesilebilir boyut isteniyorsa `nvidia/llama-nemotron-embed-vl-1b-v2` (128…2048) | doğrulandı |
| Rerank | `nvidia/llama-nemotron-rerank-vl-1b-v2` (imaj `nvcr.io/nim/nvidia/<container>:2.3`) | `POST /v1/ranking` `{"model","query":{"text"},"passages":[{"text"}],"truncate":"END"}` → `{"rankings":[{"index","logit"}]}`; skor **logit**, olasılık değil → sigmoid ile [0,1]'e çekilir | istek doğrulandı, yanıt doğrulanacak |
| LLM (NIM) | `nemotron-3.5-lightning-30b-a3b` vb. | `/v1/chat/completions`; şema kısıtı için `"nvext": {"guided_json": <schema>}` | doğrulanacak (NIM LLM "structured generation" sayfası) |
| Vision | `llama-3.2-11b-vision-instruct` | `/v1/chat/completions`, `content: [{"type":"text"},{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,…"}}]`; guided_json desteği | doğrulanacak |
| ASR | Riva ASR NIM (`whisper-large-v3`) | HTTP port 9000, gRPC 50051; OpenAI uyumlu `POST /v1/audio/transcriptions` (multipart `file`, `language`) beklenir | doğrulanacak |
| Çeviri | `riva-translate-4b-instruct-v2` | chat completions tabanlı; Türkçe destekli | doğrulanacak |

Handoff'a göre iki düzeltme: (a) embedding boyutu 2048 sabit; `EMBEDDING_DIMENSION=2048` ile
`kb_*` parmak izi değişir ve indeks yeniden kurulur, `kn_*` için yeni bir yeniden-embed yolu gerekir
(WP4). (b) Çeviri modeli handoff'ta "Endpoint" olarak işaretli; onaylı fact metni tenant verisi olduğu
için bu planda **Downloadable** olarak ele alınır (bulut kullanımı yalnız sentetik deneyde).

## 3. Mimari yerleşim

```
apps/api/src/integrations/nim/
  __init__.py
  http.py          NimHttp: base_url, api_key, timeout, tek retry, /v1/health/ready, /v1/openapi.json;
                   gövdeleri loglamaz; hata sınıfı NimError (yanıt gövdesi taşımaz)
  guardrails.py    ContentSafetyClient, JailbreakClient, TopicControlClient
  ocr.py           LayoutClient (page-elements, table-structure), OcrClient
  vision.py        VisionClient (chat completions + guided_json)
  asr.py           AsrClient
  translate.py     TranslateClient
apps/api/src/integrations/embeddings.py   + NimEmbeddingClient
apps/api/src/integrations/reranker.py     + NimReranker
apps/api/src/integrations/llm.py          + LLMRole, LLMEndpoint, get_llm_client(role), schema_mode

apps/api/src/modules/guardrails/
  ports.py         GuardDecision, GuardCheck, GuardVerdict, InputGuard (Protocol), NullInputGuard
  policy.py        karar tablosu (kategori → block/flag), konu-kontrolü prompt'u (config'ten), kısa mesaj muafiyeti
  service.py       build_input_guard(): Settings → InputGuard (lru_cache)
  turns.py         guardrail_blocked_turn(config, verdict) → RuntimeTurn (onaylı metin, response_source="guardrail")
apps/api/src/modules/knowledge/ocr.py        DocumentOcr portu + pypdfium2 rasterizasyon + bölge → TextUnit birleştirme
apps/api/src/modules/knowledge/vision.py     MediaVerifier portu + verify_media() (karar + alt metin temizliği)
apps/api/src/modules/knowledge/translation.py Translator portu + propose_translations() (aday üretimi)
apps/api/src/modules/outreach/transcription.py SpeechTranscriber portu + transcribe_inbound_audio()
```

Port/adaptör ayrımı mevcut kalıbı izler: protokoller `modules/`, HTTP adaptörleri `integrations/`,
fabrikalar `Settings` üzerinden ve `Null*` fail-closed varyantlarıyla. Runtime (`company_runtime`,
`semantic_dialogue`, `grounded_generation`) hiçbir NIM istemcisini doğrudan görmez; guardrail worker'da,
OCR/vision/çeviri ingest servisinde, ASR worker'da bağlanır.

### 3.1 Veri sınırı kapısı (WP0)

`Settings.model_endpoint_boundary_errors()` → `production_runtime_errors()` içine eklenir:

- Tenant/müşteri verisi işleyen her etkin uç (LLM rolleri, embedding, rerank, guardrail, OCR/layout,
  vision, ASR, çeviri) için `*_BASE_URL` boş olamaz ve host `NIM_PUBLIC_HOST_DENYLIST`
  (varsayılan `integrate.api.nvidia.com,ai.api.nvidia.com,api.nvcf.nvidia.com`) ile eşleşemez.
- `GUARDRAIL_ENABLED=true` iken üretimde `GUARDRAIL_FAIL_MODE=closed` zorunlu.
- `KNOWLEDGE_BACKEND=falkordb` iken `EMBEDDING_PROVIDER ∈ {ollama, nim}` (bugün yalnız `ollama`).

## 4. İş paketleri

Her WP: kapsam → dosyalar → davranış → ayarlar → migration → testler → ölçüm → kabul. WP'ler ayrı
commit'ler halinde, WP0 → WP1 → … sırasıyla teslim edilir; her WP tek başına `make lint && make test`
geçer ve varsayılan ayarlarla (**tüm NIM özellikleri kapalı**) mevcut davranışı bire bir korur.

### WP0 — Temel

- `integrations/nim/http.py`: `NimHttp(base_url, api_key, timeout)`; `post_json`, `post_multipart`,
  `ready()` (`/v1/health/ready`), `openapi()`; `httpx.AsyncClient` ile, `follow_redirects=False`;
  `NimError`/`NimUnavailableError`. Zaman aşımı ve tek retry (yalnız bağlantı hatasında).
- `Settings`: §5 tablosundaki ortak alanlar (`NIM_API_KEY`, `NIM_TIMEOUT_SECONDS`, `NIM_PUBLIC_HOST_DENYLIST`)
  ve sınır kapısı. Testi: `tests/test_production_runtime_settings.py` deseniyle denylist/host kontrolü.
- `infra/docker-compose.nim.yml` (profil `nim`, GPU sunucusunda): her NIM ayrı servis, `NGC_API_KEY`
  env, model önbelleği volume'u, yalnız özel ağa açık portlar (8010 guard-content, 8011 guard-jailbreak,
  8012 guard-topic, 8020 ocr, 8021 layout, 8030 vision, 8040 embed, 8041 rerank, 8050 llm, 9000 asr,
  8060 translate). VRAM planlaması operatör kararıdır; `docs/model-sunucusu-rehberi.md`'ye "NIM" bölümü.
- `scripts/runtime_preflight.py`: etkin her NIM ucu için `ready()` + `/v1/models` (LLM/embedding/rerank)
  sonucu `result["nim"][<rol>]`; `--require-nim` ile kapı.
- `experiments/nim/README.md` + ortak yardımcı `experiments/nim/_common.py` (rapor JSON yazımı, zamanlama).

Kabul: varsayılan ayarlarla hiçbir davranış değişmez; preflight NIM kapalıyken `nim: {}` döner.

### WP1 — Guardrail kapısı (öncelik 1)

**Runtime kancası:** `workers/agent_runtime.py::_execute_runtime_job`, "contact opted out" kontrolünden
hemen sonra, `_conversation_history_with_context`/typing indicator/`selection_service.handle`'dan **önce**:

```
verdict = await guard.check_customer_message(inbound.body, history=[…son 4 gövde…], topic_context=TopicContext.from_config(config))
if verdict.decision is BLOCK or (UNAVAILABLE and fail_mode is closed):
    turn = guardrail_blocked_turn(config, verdict)   # model çağrısı yok, selection yok
    selection_deterministic = True; …normal send yolu…
```

`job.audit["guardrail"] = verdict.audit()` her durumda yazılır (SENDING audit sözlüğüne `"guardrail"` anahtarı).
`guardrail_blocked_turn`: jailbreak/unsafe → `CustomerReplyAction.DECLINE` + `_unknown_fact_reply(config, DECLINE)`
(mevcut onaylı metin); off-topic (mode=block) → `safe_unknown_fact_turn(config, reason="guardrail:off_topic")`.
`response_source="guardrail"`, `used_fallback=True`, `fallback_reason="guardrail:<check>"`.
Bloklanan tur konuşmayı **duraklatmaz** (HANDOFF değil), böylece kötü niyetli mesaj botu kapatamaz.
Aynı kanca `modules/agents/workspace.py`'deki admin test sohbetine de takılır (parite).

**Ingest kancası:** `modules/knowledge/ingest.py::_ingest_unit` — chunk satırları oluşturulduktan sonra,
`_extract_chunks` ve `graph.upsert_chunks`'tan önce: `verdict = await guard.check_document_text(row.text)`.
BLOCK → `row.guard = verdict.audit()`, `row.extracted=False`, `row.embedded=False`, çıkarım ve graf
listelerinden çıkarılır, `stats["chunks_blocked"] += 1`; FLAG → işlenir ama `guard` yazılır. UNAVAILABLE +
closed → chunk işlenmez (`stats["chunks_guard_unavailable"]`), snapshot kalır; sync `failed` olmaz.
Konu kontrolü ingest'te çalışmaz (doküman doğal olarak konu dışı bölümler içerir).

**Karar tablosu (`policy.py`):**

| Kontrol | Girdi | BLOCK | FLAG | Not |
|---|---|---|---|---|
| jailbreak | mesaj / chunk | `jailbreak=true` ve `score ≥ GUARDRAIL_JAILBREAK_THRESHOLD` | — | Türkçe injection kalıpları için `_INJECTION_RE` regex'i bağımsız olarak kalır |
| content_safety | mesaj (+ son bot turu `Response Safety` için) / chunk | `User Safety=unsafe` ve kategori ∈ `GUARDRAIL_BLOCK_CATEGORIES` | unsafe ama kategori muaf (S9 PII: müşteri telefon/adres paylaşabilir; S12 küfür; S13/S14) | Kategori seti config; varsayılan blok listesi S1–S8, S10, S11, S15–S17, S22 |
| topic_control | yalnız mesaj, ≥ `GUARDRAIL_TOPIC_MIN_TOKENS` token | `off-topic` ve mode=block | `off-topic` ve mode=flag (varsayılan) | System prompt `config`'ten: şirket adı, `agent.purposes`, aktif offering adları, "selamlaşma/teşekkür/iletişim/fiyat/stok/teslimat soruları konu içidir" |

Kontroller paralel (`asyncio.gather`), toplam bütçe `GUARDRAIL_TIMEOUT_SECONDS` (varsayılan 2,5 sn);
biri bile zaman aşımına düşerse `UNAVAILABLE`. `GUARDRAIL_CHECKS` ile alt küme seçilebilir.

Ayarlar: §5. Migration: `knowledge_chunks.guard JSONB NOT NULL DEFAULT '{}'`.

Testler:
- `tests/test_guardrails.py`: respx ile üç NIM'in kaydedilmiş yanıtları; karar tablosu; zaman aşımı →
  UNAVAILABLE; closed/open davranışı; audit'te metin yok.
- `tests/test_knowledge_worker_integration.py`'ye: bloklanan müşteri mesajında LLM sahte istemcisi
  **hiç çağrılmaz**, job `SENT` + `audit.guardrail.decision="block"`, reply onaylı DECLINE metni;
  guardrail erişilemez + closed → `fallback_reason="guardrail_unavailable"`; open → normal akış.
- `tests/test_knowledge_ingest.py`'ye: bloklanan chunk çıkarıma/grafa girmez, `guard` dolu, sync `synced`.
- `production_runtime_errors`: enabled + open → hata.

Ölçüm: `apps/api/config/guardrail_golden.tr.json` (~120 Türkçe mesaj: normal satış soruları, selam,
küfürlü ama meşru şikâyet, PII paylaşan meşru mesaj, TR/EN jailbreak, `docs/evaluation` korpusundaki
`injection_resistance` senaryoları, konu dışı) → `experiments/nim/guardrail_eval.py` →
`experiments/nim/guardrail-report.json`: kontrol başına precision/recall, yanlış-blok oranı, p50/p95 gecikme.
Kabul: meşru mesajlarda yanlış-blok ≤ %2, jailbreak recall ≥ %90, p95 ≤ 800 ms (GPU sunucusunda).

### WP2 — OCR / tablo zinciri (öncelik 2)

- Yeni bağımlılık: `pypdfium2` (yerel rasterizasyon; pypdf görüntü üretemez). 150 DPI JPEG, ≤ 4 MB.
- `modules/knowledge/ocr.py`:
  - `pages_without_text(data, units, min_chars)` → OCR gerektiren sayfa numaraları (metin katmanı
    `< KNOWLEDGE_OCR_MIN_TEXT_CHARS`).
  - `render_pdf_page(data, page, dpi) -> bytes`.
  - `DocumentOcr` protokolü: `async read_page(image, mime) -> list[OcrRegion]`;
    `OcrRegion(kind: text|title|table, bbox: (x0,y0,x1,y1) normalize 0–1, text, cells: list[list[str]] | None, confidence)`.
  - `NimDocumentOcr` (`integrations/nim/ocr.py`): page-elements → bölgeler; `text/title` → OCR
    (satırlar üstten alta, soldan sağa); `table` → table-structure (satır/sütun/hücre kutuları) + OCR;
    kelimeler hücreye bbox merkeziyle atanır → ızgara. Layout NIM yoksa tüm sayfa tek `text` bölgesi.
  - `regions_to_units(filename, page, regions) -> list[TextUnit]` (saf): metin bölgeleri paragraf
    halinde `TextUnit(locator=f"{filename}#page={N}&bbox={x0:.4f},{y0:.4f},{x1:.4f},{y1:.4f}", meta={"page","bbox","ocr":True,"confidence"})`;
    tablo bölgeleri `extract.py::_table_rows_to_units` ile aynı biçimde (`Tablo: sayfa N` + `başlık: değer | …`),
    locator'a `&rows=a-b` eklenir; başlık satırı ilk satırdır.
- `extract.py`: `EXTRACTOR_VERSION = "2026.09.2"`; `_extract_pdf` değişmez (saf/senkron kalır);
  `_table_rows_to_units` locator'ı parametreli hale gelir ki OCR tabloları da kullansın.
- `ingest.py::_sync_documents`: `units = extract_document(...)`; `if self.ocr is not None and mime == pdf`:
  eksik sayfalar için `await self.ocr…` ile OCR birimleri eklenir, sayfa sırasına göre birleştirilir;
  `document.meta["ocr"] = {"pages": [...], "layout_model", "ocr_model", "skipped": [...]}`;
  `_MAX_CHUNKS_PER_SYNC` aynen. Daha önce `failed / no extractable text` olan dokümanlar zaten yeniden
  işlenir. Sayfa başına OCR sınırı `KNOWLEDGE_OCR_MAX_PAGES_PER_DOCUMENT`.
- Guardrail (WP1) OCR birimlerine de uygulanır (aynı `_ingest_unit` yolu).
- Çıkarım (`extraction.py`) değişmez; evidence_quote OCR metninin literal parçasıdır, locator bbox'lı.

Ayarlar: `KNOWLEDGE_OCR_ENABLED`, `KNOWLEDGE_OCR_BASE_URL`, `KNOWLEDGE_OCR_MODEL`,
`KNOWLEDGE_LAYOUT_BASE_URL`, `KNOWLEDGE_OCR_MIN_TEXT_CHARS=40`, `KNOWLEDGE_OCR_DPI=150`,
`KNOWLEDGE_OCR_MAX_PAGES_PER_DOCUMENT=60`. Migration yok (`documents.meta` mevcut).

Testler: `tests/test_knowledge_ocr.py`: Pillow ile üretilmiş görüntü-only PDF; respx'te kaydedilmiş
layout/ocr/table yanıtları; locator biçimi; tablo hücreleri → `Tablo:` satırları; layout NIM yokken tüm
sayfa OCR; OCR NIM yokken doküman `extracted` + `meta.ocr.skipped`; metin katmanı olan sayfalar OCR'a
gitmez. `test_knowledge_ingest.py`'ye: OCR birimi → chunk → aday fact (sahte LLM).

Ölçüm: `experiments/nim/fixtures/ocr/` (10 taranmış Türkçe katalog/fiyat listesi sayfası, elle yazılmış
ground truth) → `experiments/nim/ocr_eval.py` → CER/WER, tablo hücre doğruluğu, sayfa başına süre.
Kabul: CER ≤ %5 (temiz tarama), tablo hücre doğruluğu ≥ %90, paddleocr ile karşılaştırma raporlanır.

### WP3 — Görsel doğrulama (öncelik 3)

- `modules/knowledge/vision.py`: `MediaVerifier` protokolü
  `async verify(image, mime, *, subject_labels, context) -> VisionVerdict(is_product_photo, subject_id | None, confidence, alt_text | None, flags, model)`;
  `verify_media(candidate, image, labels, verifier, settings) -> MediaDecision(store: bool, subject_id, score, alt_text, verification: dict)`.
- `integrations/nim/vision.py`: chat completions + `nvext.guided_json`; şema: `subject_id` enum = bilinen
  offering id'leri + `null`; `alt_text` Türkçe ≤ 300; `is_product_photo`, `contains_text_overlay`,
  `confidence` 0–1. Model çağrısı için görsel ≤ `KNOWLEDGE_VISION_MAX_EDGE=1024` px'e küçültülür
  (saklanan görsel değişmez).
- Karar: `is_product_photo=false` ve heuristik skor < 2.0 → saklanmaz (`stats["media_rejected_vision"]`);
  model farklı `subject_id` seçti ve `confidence ≥ KNOWLEDGE_VISION_MIN_CONFIDENCE` → override;
  `score = 0.6·min(heuristik/5, 1) + 0.4·confidence`; `alt_text` yalnız mevcut alt boşsa ve
  temizlikten (kontrol karakteri, `_INJECTION_RE`, `has_protected_intent`, para birimi) geçerse yazılır.
  `verification = {"model", "confidence", "decision", "subject_before", "subject_after", "flags"}`.
- `ingest.py::_ingest_images`: `fetch_image` sonrası, `KnowledgeMedia` eklenmeden önce çağrılır;
  verifier yoksa bugünkü heuristik davranış aynen.
- `schemas.py::MediaOut.verification`, `knowledge-panel.tsx`'te "Doğrulandı · 0,91" rozeti (küçük).

Ayarlar: `KNOWLEDGE_VISION_ENABLED`, `KNOWLEDGE_VISION_BASE_URL`, `KNOWLEDGE_VISION_MODEL`,
`KNOWLEDGE_VISION_MIN_CONFIDENCE=0.6`, `KNOWLEDGE_VISION_MAX_EDGE=1024`.
Migration: `knowledge_media.verification JSONB NOT NULL DEFAULT '{}'`.

Testler: `tests/test_knowledge_vision.py` (respx; override, ret, alt-metin enjeksiyonu düşer, model
kapalıyken heuristik); `test_knowledge_ingest.py` web sitesi senaryosuna verifier eklenir.

Ölçüm: `experiments/nim/fixtures/vision/labels.json` (≥ 30 ürün/ürün-olmayan görsel, elle etiket) →
`experiments/nim/vision_eval.py` → subject eşleşme precision/recall, yanlış-ret oranı, gecikme.
Kabul: precision ≥ %90, yanlış-ret ≤ %10.

### WP4 — Embedding ve rerank adaptörleri (öncelik 4)

- `NimEmbeddingClient(base_url, api_key, model, dimension)`: `embed_query` → `input_type="query"`,
  `embed_documents` → `"passage"`, `truncate="END"`, batch 32, yanıt boyutu `dimension` ile doğrulanır.
  `dimensions` alanı yalnız `EMBEDDING_DIMENSION != native` ve model destekliyorsa gönderilir;
  `nemotron-3-embed-1b` için validator `EMBEDDING_DIMENSION=2048` zorunlu kılar.
- `embedding_provider: Literal["", "ollama", "nim"]`, `EMBEDDING_API_KEY`; `knowledge_enabled()` ve
  `production_runtime_errors()` güncellenir.
- Parmak izi: `indexer.py` → `index_fingerprint(documents, f"{model_name}#{dimension}")` ve
  `content_hash(document, f"{model_name}#{dimension}")` (mevcut bge-m3 indeksi bir kez yeniden kurulur; ~2 sn).
- `kn_<tenant>` grafiği: `EvidenceGraph.ensure_indexes` `(:Meta {embedding_model, dimension})` yazar/okur;
  profil uyuşmazlığında `EmbeddingProfileMismatchError` (ingest bu durumda graf yazmaz, `stats["graph_profile_mismatch"]`).
  Yeni Celery görevi `src.workers.knowledge.reembed_knowledge_graph(tenant_id)` + CLI
  `scripts/knowledge_reembed.py --tenant-slug`: grafı siler, indeksleri kurar, Postgres'teki
  aktif chunk'ları (`snapshot.status != removed`) 64'lük partilerle yeniden embed edip upsert eder.
  `scripts/ingest_documents.py::_stage_graph` (`cand_`) aynı boyutu kullanır (yalnız not).
- `NimReranker(base_url, api_key, model)`: `/v1/ranking`; `scores[i] = sigmoid(logit_i)`, eksik indeks 0.0;
  `RERANKER_PROVIDER: Literal["local", "nim"]`, `RERANKER_BASE_URL`, `RERANKER_API_KEY`;
  `get_reranker()` lru_cache'i ve `_warm_reranker` aynen. `CrossEncoderEntailment` aynı portu kullandığı
  için ADR-003 eşiği (0,30) NIM için yeniden kalibre edilir (aşağıda).

Testler: `tests/test_nim_embeddings.py`, `tests/test_nim_reranker.py` (respx; `input_type` doğru;
boyut uyuşmazlığı → `EmbeddingError`; logit→sigmoid; eksik indeks); `test_knowledge_retrieval.py`'de
sahte reranker ile mevcut testler kırılmaz; `reembed` için Postgres'li test (sahte embedding, sahte store).

Ölçüm (A/B): `experiments/nim/retrieval_ab.py` — dört yapılandırma
(bge-m3+local, bge-m3+nim-rerank, nim-embed+local, nim-embed+nim-rerank) için
`apps/api/config/knowledge_golden.arti_kasnak.json` + hibrit kanıt golden'ı → recall@1/@3, MRR,
retrieval p50/p95 → `experiments/nim/retrieval-ab-report.json`. Entailment eşiği: `test_grounded_generation`
örneklerinden türetilen ~40 (iddia, kanıt, beklenen) çifti ile ROC → önerilen eşik raporda.
Kabul: NIM yapılandırması ADR-002 ölçümünü (recall@3 1,00, MRR 0,96) korumuyorsa varsayılan bge-m3
kalır ve karar ADR-002'ye "Sonuçlar" notu olarak eklenir.

### WP5 — Rol bazlı LLM seçimi (öncelik 5)

- `LLMRole = Literal["customer", "generation", "extraction", "memory", "admin"]`;
  `LLMEndpoint(provider, model, base_url, api_key, schema_mode, num_ctx)`;
  `Settings.llm_endpoint(role)`: `LLM_ROLE_<ROLE>_*` doluysa onu, değilse temel `LLM_*` alanlarını döner.
  `get_llm_client(role: LLMRole = "customer")`; `LLMClient` protokolü **değişmez**.
- `ChatCompletionsLLMClient(schema_mode="response_format" | "nvext_guided_json")`: NIM'de
  `payload["nvext"] = {"guided_json": schema}`; `LLM_SCHEMA_MODE` (temel) ve rol başına.
- Çağrı noktaları: `workers/agent_runtime.py:634` ve `agents/workspace.py:501` → `customer`
  (+ `generation_llm=get_llm_client("generation")` yalnız rol yapılandırılmışsa); `workers/knowledge.py:100`
  ve `scripts/ingest_documents.py` → `extraction`; `workers/knowledge.py:196` → `memory`;
  `admin_chat/{planner,outbound,task_runner}.py`, `agents/builder_service.py` → `admin`;
  `main.py chat_test` → `customer`.
- `CompanyAgentRuntime(generation_llm: LLMClient | None = None)` → `reply_to_requests(generation_llm=)`
  → `generate_answers(llm=generation_llm or llm)`. Planlayıcı/kanıt kararları müşteri mesajını
  işlediği için `customer` rolünde kalır.
- Audit: `job.audit["models"] = {"customer": {"provider","model"}, "generation": …}` (`model`/`provider`
  anahtarları geriye dönük kalır).
- Preflight: farklı rol uçları için `/v1/models` kontrolü (tekilleştirilmiş).

Ayarlar: `LLM_SCHEMA_MODE`, `LLM_ROLE_{GENERATION,EXTRACTION,MEMORY,ADMIN}_{PROVIDER,MODEL,BASE_URL,API_KEY,SCHEMA_MODE,NUM_CTX}`.

Testler: `tests/test_llm.py`'ye rol fabrikası (fallback, override), `nvext` gövdesi (respx),
`test_grounded_generation.py`'ye "generation istemcisi yalnız üretim çağrısında kullanılır".

Ölçüm: `experiments/nim/role_llm_eval.py`:
(a) çıkarım — `test_knowledge_ingest` benzeri 5 Türkçe doküman ile Qwen3 8B vs Nemotron 3.5 Lightning:
sunucu doğrulamasından geçen aday oranı, literal alıntı oranı, **Türkçe çıktı oranı** (dil tespiti) ve
gecikme; (b) hafıza — `docs/evaluation/turkish-dialogue-gold-corpus-v1.yaml` diyaloglarından
`subject_ids`/`intent` doğruluğu (yalnız enum çıktı, dil duyarsız). Kabul: hafıza rolü Nemotron'a
güvenle geçebilir; çıkarım rolü yalnız Türkçe çıktı oranı ≥ %98 ve aday kalitesi Qwen3'ten düşük
değilse geçer, aksi halde Qwen3 kalır (rapor karara eklenir).

### WP6 — İsteğe bağlı: ses ve çeviri

**6a Ses notu → metin.**
- Migration: `ALTER TYPE message_type ADD VALUE IF NOT EXISTS 'audio'` (Alembic `autocommit_block`);
  `MessageType.AUDIO`.
- `outreach/webhooks.py::_handle_messages`: `type == "audio"` → `body=None`, `message_type=AUDIO`,
  `raw=m` (`audio.id`, `mime_type`), job normal oluşturulur (`audit.wa_message_type="audio"`; job
  oluşturma `body`'ye bağlıysa audio için de oluşturulacak şekilde düzeltilir — doğrulanacak).
- `integrations/whatsapp.py::download_media(media_id) -> (bytes, mime)`: Graph `GET /{ver}/{media_id}` →
  `url` → bearer ile GET; `ASR_MAX_AUDIO_BYTES` (16 MB) üstü reddedilir; bayt **kalıcı değildir**.
- `outreach/transcription.py`: `SpeechTranscriber` portu (`transcribe(audio, mime, *, language) -> Transcript`);
  `integrations/nim/asr.py` Riva ASR HTTP.
- `workers/agent_runtime.py`: "inbound message has no text body" dalında `message_type == AUDIO` ve
  `ASR_ENABLED` ise indir → transkript → `inbound.body = text[:4000]` (kalıcı), `job.audit["transcription"]
  = {"model","language","duration_seconds","confidence","latency_ms"}` (metin yok) → akış normal devam
  eder (guardrail transkripti görür). Transkript `_OPT_OUT_RE` ile eşleşirse webhook'taki opt-out yolu
  (yardımcı fonksiyona ayrılır) uygulanır. Boş transkript → `semantic_dialogue.clarification_text` varsa
  `ASK_CLARIFICATION`, yoksa `SKIPPED("transcription empty")`.
- Ayarlar: `ASR_ENABLED`, `ASR_BASE_URL`, `ASR_MODEL=whisper-large-v3`, `ASR_LANGUAGE=tr`,
  `ASR_MAX_AUDIO_BYTES`, `ASR_MAX_SECONDS=120`.
- Testler: respx ASR; Postgres'li worker testi (sahte transcriber, sahte media indirme) → job SENT,
  audit'te metin yok, `Message.body` transkript; opt-out transkripti. Ölçüm: 20 Türkçe WhatsApp tarzı
  ses notu (ekip kaydı) → WER, gecikme; kabul WER ≤ %15.

**6b Onaylı fact çevirisi (aday olarak).**
- `modules/knowledge/translation.py`: `Translator` portu; `propose_translations(session, tenant, agent, locale)`:
  `customer_visible` ve `customer_text[locale]` eksik fact'ler → `KnowledgeCandidate(kind="translation",
  subject_id=fact.subject_id, category=fact.category, payload={"fact_id","locale","customer_text"},
  evidence={"source_locale","source_text_hash"}, confidence, protected=fact.protected veya sayı/kod
  çapaları (`text_anchors`) kaynakla birebir değilse)`. **Asla otomatik yayınlanmaz**.
- Publisher: `ConfigPatcher.add_translation(fact_id, locale, text)` → `fact.customer_text[locale]`;
  `accept_candidate` yolu `kind="translation"`'ı tanır; `agent.supported_locales` locale'i içermiyorsa
  aday `error` ile bekler. Admin chat `knowledge_review` listesinde `[Çeviri · en] …` etiketi.
- Tetik: `POST /api/v1/knowledge/agents/{agent_id}/translations {locale}` (manager) → `knowledge` kuyruğu.
- Ayarlar: `TRANSLATION_ENABLED`, `TRANSLATION_BASE_URL`, `TRANSLATION_MODEL`.
- Testler: aday üretimi, çapa uyuşmazlığı → protected, accept → LIVE `customer_text[en]`, yayın asla otomatik değil.
  Ölçüm: 40 onaylı Artı Kasnak fact'i TR→EN, iki dilli değerlendirici puanı + çapa koruma oranı (%100 zorunlu).

## 5. Ayar tablosu (yeni `Settings` alanları)

| Env | Tip / varsayılan | Kullanan |
|---|---|---|
| `NIM_API_KEY` | str, "" | tüm NIM istemcileri (rol başına `*_API_KEY` öncelikli) |
| `NIM_TIMEOUT_SECONDS` | float, 10 | `NimHttp` |
| `NIM_PUBLIC_HOST_DENYLIST` | str, `integrate.api.nvidia.com,ai.api.nvidia.com,api.nvcf.nvidia.com` | sınır kapısı |
| `GUARDRAIL_ENABLED` | bool, false | WP1 |
| `GUARDRAIL_FAIL_MODE` | `closed|open`, closed | WP1 |
| `GUARDRAIL_TIMEOUT_SECONDS` | float, 2.5 | WP1 |
| `GUARDRAIL_CHECKS` | str, `jailbreak,content_safety,topic_control` | WP1 |
| `GUARDRAIL_INGEST_ENABLED` | bool, true | WP1 (ingest) |
| `GUARDRAIL_CONTENT_SAFETY_BASE_URL/MODEL` | str | WP1 |
| `GUARDRAIL_BLOCK_CATEGORIES` | str, `S1,S2,S3,S4,S5,S6,S7,S8,S10,S11,S15,S16,S17,S22` | WP1 |
| `GUARDRAIL_JAILBREAK_BASE_URL` | str | WP1 |
| `GUARDRAIL_JAILBREAK_THRESHOLD` | float, 0.0 | WP1 |
| `GUARDRAIL_TOPIC_CONTROL_BASE_URL/MODEL` | str | WP1 |
| `GUARDRAIL_TOPIC_CONTROL_MODE` | `flag|block`, flag | WP1 |
| `GUARDRAIL_TOPIC_MIN_TOKENS` | int, 3 | WP1 |
| `KNOWLEDGE_OCR_ENABLED` | bool, false | WP2 |
| `KNOWLEDGE_OCR_BASE_URL`, `KNOWLEDGE_OCR_MODEL`, `KNOWLEDGE_LAYOUT_BASE_URL` | str | WP2 |
| `KNOWLEDGE_OCR_MIN_TEXT_CHARS` | int, 40 | WP2 |
| `KNOWLEDGE_OCR_DPI` | int, 150 | WP2 |
| `KNOWLEDGE_OCR_MAX_PAGES_PER_DOCUMENT` | int, 60 | WP2 |
| `KNOWLEDGE_VISION_ENABLED` | bool, false | WP3 |
| `KNOWLEDGE_VISION_BASE_URL`, `KNOWLEDGE_VISION_MODEL` | str | WP3 |
| `KNOWLEDGE_VISION_MIN_CONFIDENCE` | float, 0.6 | WP3 |
| `KNOWLEDGE_VISION_MAX_EDGE` | int, 1024 | WP3 |
| `EMBEDDING_PROVIDER` | `""|ollama|nim` | WP4 |
| `EMBEDDING_API_KEY` | str | WP4 |
| `RERANKER_PROVIDER` | `local|nim`, local | WP4 |
| `RERANKER_BASE_URL`, `RERANKER_API_KEY` | str | WP4 |
| `LLM_SCHEMA_MODE` | `response_format|nvext_guided_json`, response_format | WP5 |
| `LLM_ROLE_<GENERATION|EXTRACTION|MEMORY|ADMIN>_<PROVIDER|MODEL|BASE_URL|API_KEY|SCHEMA_MODE|NUM_CTX>` | str/int, boş = temel `LLM_*` | WP5 |
| `ASR_ENABLED`, `ASR_BASE_URL`, `ASR_MODEL`, `ASR_LANGUAGE`, `ASR_MAX_AUDIO_BYTES`, `ASR_MAX_SECONDS` | — | WP6a |
| `TRANSLATION_ENABLED`, `TRANSLATION_BASE_URL`, `TRANSLATION_MODEL` | — | WP6b |

## 6. Migration listesi

1. WP1: `knowledge_chunks.guard JSONB NOT NULL DEFAULT '{}'`.
2. WP3: `knowledge_media.verification JSONB NOT NULL DEFAULT '{}'`.
3. WP6a: `message_type` enum'una `audio` değeri (`autocommit_block`).

Yeni tablo yoktur; guardrail olaylarını ayrı tabloda tutma ihtiyacı doğarsa `b7c4d9e2f013` deseniyle
RLS + `GRANT … TO leadpulse_app` zorunludur.

## 7. Deploy

- GPU sunucusu (Linux + NVIDIA sürücü + NVIDIA Container Toolkit), `docker compose -f infra/docker-compose.nim.yml --profile nim up -d`.
  `NGC_API_KEY` yalnız o sunucunun `.env`'inde. Portlar yalnız özel ağ/VPN'e açılır; uygulama sunucusu
  mevcut Ollama tüneli gibi erişir (`docs/runbook.md` "Arch host" deseni).
- Uygulama `.env`: yalnız etkinleştirilen WP'lerin `*_BASE_URL` alanları; kalanı boş → özellik kapalı.
- `runtime_preflight.py --require-nim` üretim kapısına eklenir.
- VRAM ölçeklendirme (kabaca): guard 8B ×3 ≈ 3×16 GB (fp16) veya fp8 ile yarısı; vision 11B ≈ 22 GB;
  Nemotron 3.5 Lightning 30B-A3B ≈ 60 GB fp16 / ~30 GB fp8; embed/rerank ≤ 4 GB; OCR/layout ≤ 4 GB.
  Tek 80 GB GPU'da aynı anda hepsi sığmaz; WP sırasına göre kademeli açılır. Operatör kararı.

## 8. Riskler ve açık kararlar

- **Türkçe.** NemoGuard/Nemotron modellerinin resmî dil listesinde Türkçe zayıf. Bu yüzden WP1 ve WP5
  Türkçe golden set ölçümü olmadan üretime alınmaz; guardrail için regex süzgeci kalır, konu kontrolü
  varsayılan `flag`.
- **Yanlış-blok maliyeti.** Guardrail bloklaması satış kaybıdır; kabul eşiği (%2) ve `flag` varsayılanı bu yüzden.
- **Embedding boyutu.** 2048 boyut FalkorDB HNSW belleğini iki katına çıkarır; `kb_` küçük (63 fact),
  `kn_` chunk sayısına bağlı (400/sync). Rapora bellek ölçümü eklenir.
- **API şekilleri.** §2'de "doğrulanacak" satırlar konteyner OpenAPI'siyle sabitlenir; fixture'lar
  `tests/fixtures/nim/*.json` olarak kaydedilir ve testler yalnız onları kullanır.
- **Gecikme bütçesi.** ADR-002 toplam retrieval 300–400 ms; guardrail +≤ 800 ms p95 ile müşteri turu
  hâlâ 2–4 sn bandında. Guardrail worker'da typing indicator'dan önce çalıştığı için müşteri "yazıyor"
  görmeden önce ~0,5 sn bekler; kabul edilebilir, ölçülür.
- **Ses saklama.** Ses baytı hiçbir yerde kalıcı değildir; yalnız transkript `Message.body`'de. KVKK
  metnine "sesli mesajlar metne dönüştürülür" notu (docs/compliance.md) eklenir.

## 9. Sıra ve bağımlılıklar

```
WP0 ──► WP1 ──► WP2 ──► WP3 ──► WP4 ──► WP5 ──► WP6a / WP6b
         │       │              │
         │       └─ guardrail OCR sayfalarına da uygulanır
         └─ WP5 rol seçimi guardrail'den bağımsız; WP4 A/B WP5'ten önce biter
```

Her WP sonunda: `make lint && make test`, ilgili `experiments/nim/*-report.json`, ADR/architecture notu
ve `docs/runbook.md`'ye ayar/operasyon satırı. WP1–WP5 tamamlandığında ADR-004
("NIM harness ajanları ve veri sınırı") yazılır; WP6 ayrı ADR gerektirmez.
