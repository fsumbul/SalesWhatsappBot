# Kodlama ajanı prompt'u — NVIDIA NIM harness ajanları (2026-09-16)

> Bu dosya olduğu gibi bir kodlama ajanına verilir. Plan ve gerekçe
> [`nvidia-nim-harness-agents-plan-2026-09-16.md`](./nvidia-nim-harness-agents-plan-2026-09-16.md)
> içindedir; prompt o planı uygular.

---

## Rol

Sen `SalesWhatsappBot` (Ashira AI) monorepo'sunda çalışan kıdemli bir backend mühendisisin. Dal:
`feat/graphrag-self-service-knowledge` (origin'de). Görevin, build.nvidia.com kataloğundan seçilmiş
NVIDIA NIM modellerini mevcut GraphRAG + self-servis bilgi mimarisine **harness ajanları** olarak
eklemek: sınıflandıran, çıkaran, dönüştüren; ama **asla karar vermeyen ve müşteriye metin yazmayan**
model çağrıları. Mevcut "model fact ID seçer, sunucu literal metni render eder" sözleşmesi ve ADR-003
denetim zinciri hiçbir adımda gevşemez.

Türkçe yaz (kod yorumları ve docstring'ler mevcut dosyalardaki gibi İngilizce kalabilir; doküman,
commit gövdesi ve rapor Türkçe). Soru sormadan önce repoyu oku; yalnız aşağıdaki "dur ve sor"
noktalarında dur.

## Önce oku (bu sırayla, kod yazmadan)

1. `docs/nvidia-nim-harness-agents-plan-2026-09-16.md` — iş paketleri, arayüzler, ayar tablosu, kabul ölçütleri.
2. `docs/nvidia-nim-agents-handoff-2026-09-16.md` — katalog taraması ve rol→model eşlemesi.
3. `docs/adr/ADR-002-falkordb-graphrag-retrieval.md`, `docs/adr/ADR-003-self-service-knowledge-and-hybrid-answers.md`.
4. `docs/project-handoff.md` §1 ve §3 ("LLM boundary": müşteri mesajı buluta gitmez; fine-tuning yok).
5. Kod: `apps/api/src/core/config.py`, `apps/api/src/integrations/{llm,embeddings,reranker}.py`,
   `apps/api/src/modules/knowledge/{ports,service,ingest,extract,media,extraction,evidence,indexer,compiler}.py`,
   `apps/api/src/workers/{agent_runtime,knowledge}.py`, `apps/api/src/modules/agents/{grounded_audit,grounded_types,company_runtime}.py`
   (özellikle `safe_unknown_fact_turn`, `_unknown_fact_reply`, `RuntimeTurn`), `apps/api/src/modules/outreach/webhooks.py`
   (`_handle_messages`, `_message_body`, `_OPT_OUT_RE`), `apps/api/alembic/versions/b7c4d9e2f013_knowledge_sources.py`.
6. Testler: `apps/api/tests/{test_llm,test_knowledge_ingest,test_knowledge_worker_integration,test_grounded_generation,test_production_runtime_settings}.py`
   — sahte LLM (`_ScriptedLLM`), respx, Postgres'li fixture (`runtime_database`, `_seed_runtime_tenant`) kalıplarını aynen kullan.

## Değişmezler (ihlal edilirse iş kabul edilmez)

- **Veri sınırı.** Müşteri mesajı, tenant dokümanı/sayfası/görseli/ses notu yalnızca operatörün kendi
  GPU sunucusundaki Downloadable NIM'e gider. `integrate.api.nvidia.com` ve türevleri uygulama
  kodundan **hiç** çağrılmaz; yalnız `experiments/nim/` altındaki sentetik değerlendirme scriptleri
  `--allow-cloud` bayrağıyla ve sentetik veriyle kullanabilir. Bu kural `Settings.production_runtime_errors()`
  içinde kodla zorlanır (plan §3.1).
- **Müşteri metni tek kaynaktan.** Hiçbir NIM çıktısı doğrudan `RuntimeTurn.reply`'a girmez. Guardrail
  bloklarında onaylı `_unknown_fact_reply(config, DECLINE)` veya `safe_unknown_fact_turn` metni kullanılır.
- **Fail-closed.** Guardrail erişilemezse model çağrılmaz. OCR/vision/ASR/çeviri erişilemezse ilgili
  girdi sessizce atlanır ve `stats`/`audit`/`meta`'ya yazılır; asla uydurma sonuç, asla handoff.
- **Varsayılan = kapalı.** Tüm yeni özellikler `Settings` varsayılanlarıyla kapalıdır; mevcut test
  paketi değişmeden yeşil kalır.
- **Audit gizliliği.** `AgentRuntimeJob.audit`, `knowledge_chunks.guard`, `knowledge_media.verification`
  yalnız karar/etiket/skor/model/süre içerir; müşteri metni, görsel/ses baytı, sağlayıcı yanıt gövdesi
  içermez. İstisnalar (`NimError`, `LLMCompletionError` konvansiyonu) gövde taşımaz; loglar da.
- **Ayar tek yerde.** Her yeni ayar `Settings` alanı + kök `.env.example` + `apps/api/.env.example`
  (ikisi ayrı yorum stiline sahip, ikisini de güncelle). Env'i başka yerde okuma.
- **Şema kısıtı.** Model çıktıları her zaman JSON şemasıyla kısıtlanır (Ollama `format`, chat-compatible
  `response_format`, NIM `nvext.guided_json`) ve pydantic ile yeniden doğrulanır; şema dışı → hata → fail-closed.
- **Tenant izolasyonu.** Yeni tablo gerekirse `b7c4d9e2f013` deseniyle RLS + `GRANT … TO leadpulse_app`.
  Bu planda yalnız sütun eklemeleri var; sütunlar mevcut RLS'li tablolara girer.
- **Kalite.** `cd apps/api && poetry run ruff check . && poetry run mypy src && poetry run pytest -v`
  her WP sonunda yeşil. mypy strict; `Any` yalnız mevcut dosyaların kullandığı sınırlarda.
- **Fine-tuning yok, LoRA yok.**

## Çalışma yöntemi (her adaptör için aynı döngü)

1. **Doğrula:** ilgili NIM konteynerini GPU sunucusunda çalıştır (`infra/docker-compose.nim.yml`, WP0),
   `GET /v1/openapi.json` ve `GET /v1/health/ready` çıktısını oku; plan §2'de "doğrulanacak" işaretli
   istek/yanıt şeklini sabitle. Konteynere erişimin yoksa: model kartı + NIM dokümanından şekli yaz,
   fixture'ı "unverified" etiketiyle kaydet ve dur-ve-sor listesine ekle.
2. **Fixture:** gerçek (sentetik girdiyle alınmış) yanıtı `apps/api/tests/fixtures/nim/<adapter>_<case>.json`
   olarak kaydet. Testler yalnız bu fixture'ları respx ile kullanır; ağ yok.
3. **Adaptör:** `apps/api/src/integrations/nim/<x>.py`, `NimHttp` üzerinden; `available` özelliği;
   `Null<X>` fail-closed varyantı; zaman aşımı; tek retry yalnız bağlantı hatasında.
4. **Port + fabrika:** protokol `apps/api/src/modules/<alan>/…`, fabrika `Settings` → port (`lru_cache`
   yalnız süreç içi ağır nesneler için, `get_reranker` gibi).
5. **Kanca:** planda belirtilen tek noktaya bağla; runtime modülleri (`company_runtime`, `semantic_dialogue`,
   `grounded_generation`) NIM istemcisini görmez.
6. **Test:** birim (respx) + Postgres'li entegrasyon (mevcut fixture'lar) + `production_runtime_errors` kapısı.
7. **Ölçüm:** `experiments/nim/<wp>_eval.py` → `experiments/nim/<wp>-report.json` (Türkçe golden set,
   plan §4 kabul eşikleri). Rapor yoksa WP bitmemiştir.
8. **Doküman:** ADR-002/003'e "Sonuçlar" notu veya ADR-004 taslağı, `docs/architecture.md` bölümü,
   `docs/runbook.md` ayar/operasyon satırı, `docs/model-sunucusu-rehberi.md` NIM bölümü.
9. **Commit:** WP başına bir commit (`feat(nim): WP1 guardrail gate …`), gövdede kabul testleri ve
   rapor özeti; Co-Authored-By satırı kurallara göre.

---

## WP0 — Temel (önce bu)

Yap:
- `apps/api/src/integrations/nim/__init__.py`, `http.py`: `NimHttp(base_url, api_key, timeout_seconds)`
  ile `post_json(path, payload) -> dict`, `post_multipart(path, files, data) -> dict`, `ready() -> bool`,
  `openapi() -> dict`. `httpx.AsyncClient(follow_redirects=False)`; `Authorization: Bearer` yalnız key doluysa.
  `NimError(RuntimeError)`, `NimUnavailableError(NimError)`. Yanıt gövdesi hiçbir istisna/log'a girmez.
- `Settings`: `nim_api_key`, `nim_timeout_seconds=10.0`, `nim_public_host_denylist` (plan §5) ve
  `model_endpoint_boundary_errors() -> list[str]`; `production_runtime_errors()` bunu ekler. Etkin her
  özelliğin `*_base_url`'i boşsa veya host denylist'teyse hata. `GUARDRAIL_ENABLED` + üretim →
  `GUARDRAIL_FAIL_MODE=closed` zorunlu. `knowledge_backend=falkordb` → `embedding_provider ∈ {ollama, nim}`.
- `infra/docker-compose.nim.yml` (profil `nim`): plan §7 port planı; `NGC_API_KEY` env; model cache volume;
  `deploy.resources.reservations.devices` ile GPU. Yorumlarda VRAM notu ve "yalnız özel ağ" uyarısı.
- `scripts/runtime_preflight.py`: `result["nim"] = {<rol>: {"reachable", "ready", "models"?}}`;
  `--require-nim` bayrağı `_failed`'e eklenir. `tests/test_runtime_preflight.py` genişletilir.
- `experiments/nim/README.md`, `experiments/nim/_common.py` (rapor yazımı, zamanlama, `--allow-cloud` bayrağı
  ve sentetik veri uyarısı).

Kabul: varsayılanlarla davranış değişmez; `tests/test_production_runtime_settings.py`'ye denylist ve
fail-mode testleri; preflight NIM kapalıyken `nim: {}`.

## WP1 — Guardrail kapısı

Yap:
- `apps/api/src/modules/guardrails/ports.py`:
  ```python
  class GuardDecision(StrEnum): ALLOW="allow"; FLAG="flag"; BLOCK="block"; UNAVAILABLE="unavailable"
  @dataclass(frozen=True) class GuardCheck: name: str; decision: GuardDecision; score: float | None; labels: tuple[str, ...]; latency_ms: float; model: str
  @dataclass(frozen=True) class GuardVerdict:
      decision: GuardDecision; checks: tuple[GuardCheck, ...]; reason: str | None = None
      def audit(self) -> dict[str, object]  # metin yok
  @dataclass(frozen=True) class TopicContext: company_name: str; purposes: tuple[str, ...]; offering_labels: tuple[str, ...]; locale: str
  class InputGuard(Protocol):
      @property
      def available(self) -> bool: ...
      async def check_customer_message(self, text: str, *, history: list[str], topic: TopicContext | None) -> GuardVerdict: ...
      async def check_document_text(self, text: str) -> GuardVerdict: ...
  class NullInputGuard: available=False → her çağrı GuardVerdict(ALLOW, ())  # özellik kapalı = mevcut davranış
  ```
- `integrations/nim/guardrails.py`: `JailbreakClient` (`POST /v1/classify` `{"input"}` → `{"jailbreak","score"}`),
  `ContentSafetyClient` (`/v1/chat/completions`; S1–S23 taksonomili şablon; JSON `"User Safety"`,
  `"Response Safety"`, `"Safety Categories"` parse; JSON değilse `unsafe`/`safe` token'ı ara; ikisi de
  yoksa UNAVAILABLE), `TopicControlClient` (`/v1/chat/completions`; system prompt `policy.topic_prompt(TopicContext)`
  ile, sonu tam olarak `If any of the above conditions are violated, please respond with "off-topic". Otherwise, respond with "on-topic".`;
  çıktı `on-topic|off-topic` dışında → UNAVAILABLE).
- `modules/guardrails/policy.py`: karar tablosu (plan WP1 tablosu), `GUARDRAIL_BLOCK_CATEGORIES`,
  `GUARDRAIL_TOPIC_CONTROL_MODE`, `GUARDRAIL_TOPIC_MIN_TOKENS` (kısa mesajlar konu kontrolünden muaf),
  `combine(checks, fail_mode) -> GuardVerdict` (herhangi bir kontrol UNAVAILABLE ve closed → UNAVAILABLE;
  BLOCK > FLAG > ALLOW). Kontroller `asyncio.gather` + `asyncio.wait_for(GUARDRAIL_TIMEOUT_SECONDS)`.
- `modules/guardrails/service.py`: `build_input_guard() -> InputGuard` (Settings'ten; kapalıysa `NullInputGuard`).
- `modules/guardrails/turns.py`: `guardrail_blocked_turn(config, verdict) -> RuntimeTurn`:
  jailbreak/unsafe → `action=DECLINE`, `reply=_unknown_fact_reply(config, CustomerReplyAction.DECLINE)`,
  `fact_ids=()`; off-topic (block) veya UNAVAILABLE → `safe_unknown_fact_turn(config, reason=…)`;
  hepsinde `used_fallback=True`, `response_source="guardrail"`, `fallback_reason=f"guardrail:{reason}"`.
  Bloklanan tur **HANDOFF üretmez** (konuşma duraklamaz).
- Kanca (runtime): `workers/agent_runtime.py::_execute_runtime_job`, "contact opted out" kontrolünden
  hemen sonra; typing indicator, `selection_service.handle` ve `CompanyAgentRuntime` **öncesi**.
  `history` olarak son 4 mesaj gövdesi. BLOCK/UNAVAILABLE(closed) → `turn = guardrail_blocked_turn(…)`,
  `selection_deterministic = True`, akış normal send yoluna devam. Her durumda SENDING audit sözlüğüne
  `"guardrail": verdict.audit()` eklenir. `job.audit["model"]` bloklu turda `"guardrail"`.
  Aynı kancayı `modules/agents/workspace.py`'deki admin test sohbetine ekle.
- Kanca (ingest): `modules/knowledge/ingest.py::_ingest_unit`: chunk satırları oluşturulduktan sonra,
  `_extract_chunks` ve `graph.upsert_chunks` **öncesi** `check_document_text(row.text)`. BLOCK →
  `row.guard = audit`, `extracted=False`, `embedded=False`, çıkarım/graf listelerinden çıkar,
  `stats["chunks_blocked"]`; FLAG → işlenir, `guard` yazılır; UNAVAILABLE+closed → chunk işlenmez,
  `stats["chunks_guard_unavailable"]`, sync `failed` olmaz. `KnowledgeIngestService(guard: InputGuard | None)`
  parametresi; `workers/knowledge.py::_sync_knowledge_source` `build_input_guard()` geçer
  (`GUARDRAIL_INGEST_ENABLED`).
- Migration: `knowledge_chunks.guard JSONB NOT NULL DEFAULT '{}'`; `KnowledgeChunk.guard`.
- Ayarlar: plan §5 GUARDRAIL_*; `.env.example` ×2.
- Golden set: `apps/api/config/guardrail_golden.tr.json` (~120 kayıt: `{"text","expected":"allow|flag|block","kind"}`),
  `docs/evaluation/turkish-dialogue-gold-corpus-v1.yaml` `injection_resistance` senaryolarından türet.
- `experiments/nim/guardrail_eval.py` → `experiments/nim/guardrail-report.json` (kontrol başına P/R,
  yanlış-blok oranı, p50/p95).

Kabul: `tests/test_guardrails.py` (karar tablosu, zaman aşımı, closed/open, audit'te metin yok);
`test_knowledge_worker_integration.py`'de bloklanan mesajda sahte LLM **hiç çağrılmaz**, job SENT,
reply onaylı DECLINE metni, `audit.guardrail.decision="block"`; UNAVAILABLE+closed →
`fallback_reason="guardrail:unavailable"`; open → normal akış; `test_knowledge_ingest.py`'de
bloklu chunk çıkarıma/grafa girmez. Ölçüm eşikleri: yanlış-blok ≤ %2, jailbreak recall ≥ %90, p95 ≤ 800 ms.

## WP2 — OCR / tablo zinciri

Yap:
- `pyproject.toml`: `pypdfium2` bağımlılığı (poetry lock güncelle).
- `modules/knowledge/ocr.py`: `pages_without_text(data, units, *, min_chars) -> list[int]`,
  `render_pdf_page(data, page, *, dpi) -> bytes` (JPEG, ≤ 4 MB), `OcrRegion` (plan WP2), `DocumentOcr`
  protokolü, `NullDocumentOcr`, `regions_to_units(filename, page, regions) -> list[TextUnit]` (saf; locator
  `f"{filename}#page={N}&bbox={x0:.4f},{y0:.4f},{x1:.4f},{y1:.4f}"`, tablo için `&rows=a-b`;
  `meta={"page","bbox","ocr":True,"confidence","kind"}`), `ocr_pdf_pages(filename, data, pages, ocr, *, dpi, max_pages) -> list[TextUnit]`.
- `extract.py`: `EXTRACTOR_VERSION="2026.09.2"`; `_table_rows_to_units` locator üretimini parametreli
  yap (`locator_for(first, last) -> str`) ki OCR tabloları aynı `Tablo:` biçimini kullansın; `_extract_pdf` aynen.
- `integrations/nim/ocr.py`: `LayoutClient` (`POST /v1/page-elements`, `POST /v1/table-structure`;
  istek `{"input":[{"type":"image_url","url":"data:image/jpeg;base64,…"}]}`; yanıt bbox/sınıf/güven —
  konteyner OpenAPI'siyle sabitle), `OcrClient` (`nemotron-ocr-v2`; endpoint'i OpenAPI'den sabitle),
  `NimDocumentOcr(layout, ocr)`: page-elements → `text/title` bölgeleri OCR, `table` bölgeleri
  table-structure + OCR (kelime → hücre, bbox merkezi), layout yoksa tüm sayfa tek bölge.
  Koordinatlar her zaman 0–1'e normalize edilir (piksel geldiyse görüntü boyutuna böl).
- `ingest.py::_sync_documents`: PDF ve `self.ocr is not None` ise eksik sayfalar OCR'lanır, birimler
  sayfa sırasına göre birleşir; `document.meta["ocr"] = {"pages", "skipped", "layout_model", "ocr_model", "dpi"}`;
  OCR erişilemezse doküman metin katmanı olan sayfalarla `extracted` olur, hiç metin yoksa mevcut
  `failed / no extractable text` korunur. `KnowledgeIngestService(ocr: DocumentOcr | None)`.
- Ayarlar: KNOWLEDGE_OCR_*, KNOWLEDGE_LAYOUT_BASE_URL (plan §5).

Kabul: `tests/test_knowledge_ocr.py` (Pillow ile üretilmiş görüntü-only PDF; fixture'lı layout/ocr/table
yanıtları; locator biçimi; tablo hücreleri → `Tablo:` satırları; layout yokken tam sayfa; OCR yokken
`meta.ocr.skipped`; metin katmanlı sayfa OCR'a gitmez); `test_knowledge_ingest.py`'de OCR birimi →
chunk → aday fact. Ölçüm: `experiments/nim/ocr_eval.py` (10 taranmış Türkçe sayfa, ground truth
`experiments/nim/fixtures/ocr/`), CER ≤ %5, tablo hücre doğruluğu ≥ %90; paddleocr karşılaştırması raporda.

## WP3 — Görsel doğrulama

Yap:
- `modules/knowledge/vision.py`: `VisionVerdict(is_product_photo, subject_id, confidence, alt_text, flags, model)`,
  `MediaVerifier` protokolü (`verify(image, mime, *, subject_labels, context) -> VisionVerdict`), `NullMediaVerifier`,
  `verify_media(candidate: ImageCandidate, image: FetchedImage, labels, verifier, settings) -> MediaDecision(store, subject_id, score, alt_text, verification)`.
  Karar kuralları plan WP3: ret, override (`confidence ≥ KNOWLEDGE_VISION_MIN_CONFIDENCE`),
  `score = 0.6·min(heuristik/5,1) + 0.4·confidence`, alt metin temizliği (kontrol karakteri,
  `grounded_audit._INJECTION_RE`, `has_protected_intent`, para birimi → alt yazılmaz, `flags` işaretlenir).
- `integrations/nim/vision.py`: chat completions, `content=[{"type":"text",…},{"type":"image_url","image_url":{"url":"data:…;base64,…"}}]`,
  `nvext.guided_json` şeması: `{"is_product_photo": bool, "subject_id": enum(labels)|null, "confidence": 0..1, "alt_text": str≤300, "contains_text_overlay": bool}`;
  görsel model çağrısı için `KNOWLEDGE_VISION_MAX_EDGE` px'e küçültülür (saklanan orijinal kalır).
  Guided JSON desteklenmiyorsa `response_format` dene; ikisi de yoksa pydantic doğrulaması + hata → atla.
- `ingest.py::_ingest_images`: `fetch_image` sonrası, `KnowledgeMedia` eklenmeden önce `verify_media`;
  verifier yoksa bugünkü davranış. `KnowledgeMedia.verification` doldurulur. `KnowledgeIngestService(vision: MediaVerifier | None)`.
- Migration: `knowledge_media.verification JSONB NOT NULL DEFAULT '{}'`. `schemas.py::MediaOut.verification`,
  `apps/web/src/components/workspace/knowledge-panel.tsx` küçük rozet ("Doğrulandı · 0,91" / "Model kapalı").
- Ayarlar: KNOWLEDGE_VISION_* (plan §5).

Kabul: `tests/test_knowledge_vision.py` (override, ret, alt-metin enjeksiyonu düşer, kapalıyken heuristik);
`test_knowledge_ingest.py` site senaryosuna verifier. Ölçüm: `experiments/nim/vision_eval.py`,
`experiments/nim/fixtures/vision/labels.json` (≥ 30 görsel), precision ≥ %90, yanlış-ret ≤ %10.

## WP4 — Embedding ve rerank adaptörleri

Yap:
- `integrations/embeddings.py`: `NimEmbeddingClient(base_url, api_key, model, dimension, *, native_dimension: int | None)`;
  `embed_query` → `input_type="query"`, `embed_documents` → `"passage"`; `truncate="END"`, `encoding_format="float"`;
  `dimensions` yalnız `dimension != native_dimension` ve model destekliyorsa; batch 32; yanıt boyutu kontrolü.
  `embedding_provider: Literal["", "ollama", "nim"]`, `embedding_api_key`. `nvidia/nemotron-3-embed-1b` için
  validator: `EMBEDDING_DIMENSION` 2048 değilse hata (**model kesme desteklemez**; plan §2 düzeltmesi).
- `knowledge/service.py::knowledge_enabled()` ve `Settings.production_runtime_errors()`: `nim` kabul edilir.
- Parmak izi: `indexer.py` → `index_fingerprint(documents, f"{model_name}#{dimension}")`,
  `content_hash(document, f"{model_name}#{dimension}")`; `compiler.py` imzaları değişmez (profil dizesi
  çağıran tarafta oluşur). Not: mevcut bge-m3 indeksi bir kez yeniden kurulur.
- `evidence.py::EvidenceGraph.ensure_indexes`: `(:Meta {embedding_model, dimension})` oku/yaz;
  uyuşmazlıkta `EmbeddingProfileMismatchError`; `ingest.py` bunu yakalar → `stats["graph_profile_mismatch"]`,
  chunk'lar `embedded=False` kalır, sync devam eder.
- `workers/knowledge.py::reembed_knowledge_graph(tenant_id)` + `scripts/knowledge_reembed.py --tenant-slug`:
  `kn_<tenant>` grafını sil, `ensure_indexes`, Postgres'teki aktif chunk'ları (`snapshot.status != "removed"`)
  64'lük partilerle `upsert_chunks`, `embedded=True`. `Makefile`: `knowledge-reembed`.
- `integrations/reranker.py`: `NimReranker(base_url, api_key, model)`; `POST /v1/ranking`
  `{"model","query":{"text"},"passages":[{"text"}],"truncate":"END"}` → `rankings[{index,logit}]`;
  `scores[index] = 1/(1+exp(-logit))`, eksik indeks 0.0; `available=True`; `RERANKER_PROVIDER: Literal["local","nim"]`,
  `RERANKER_BASE_URL`, `RERANKER_API_KEY`. `get_reranker()` ve `_warm_reranker` değişmeden çalışır.
- Ayarlar: plan §5.

Kabul: `tests/test_nim_embeddings.py`, `tests/test_nim_reranker.py` (respx; `input_type`; boyut
uyuşmazlığı → `EmbeddingError`; sigmoid; eksik indeks); mevcut retrieval/evidence testleri yeşil;
`reembed` için Postgres'li test (sahte embedding + sahte store). Ölçüm: `experiments/nim/retrieval_ab.py`
dört yapılandırma × `apps/api/config/knowledge_golden.arti_kasnak.json` (+ hibrit kanıt golden'ı)
→ recall@1/@3, MRR, p50/p95, FalkorDB bellek; entailment eşiği için ~40 (iddia, kanıt, beklenen) çiftiyle
ROC ve önerilen eşik → `experiments/nim/retrieval-ab-report.json`. Kabul: recall@3 ≥ 1,00 ve MRR ≥ 0,96
korunmuyorsa varsayılan bge-m3 kalır; karar ADR-002 "Sonuçlar"a eklenir.

## WP5 — Rol bazlı LLM seçimi

Yap:
- `integrations/llm.py`: `LLMRole = Literal["customer","generation","extraction","memory","admin"]`,
  `@dataclass(frozen=True) LLMEndpoint(provider, model, base_url, api_key, schema_mode, num_ctx)`;
  `Settings.llm_endpoint(role) -> LLMEndpoint` (rol alanları boşsa temel `LLM_*`); `get_llm_client(role: LLMRole = "customer")`.
  `ChatCompletionsLLMClient(..., schema_mode: Literal["response_format","nvext_guided_json"]="response_format")`:
  NIM modunda `payload["nvext"] = {"guided_json": response_schema}` (ve `response_format` gönderilmez).
  `LLMClient` protokolü **değişmez**.
- Çağrı noktaları: `workers/agent_runtime.py` (`customer`; `generation_llm=get_llm_client("generation")`
  yalnız `LLM_ROLE_GENERATION_MODEL` doluysa), `agents/workspace.py` (aynı), `workers/knowledge.py`
  (`extraction`, `memory`), `scripts/ingest_documents.py` (`extraction`), `admin_chat/{planner,outbound,task_runner}.py`
  ve `agents/builder_service.py` (`admin`), `main.py chat_test` (`customer`).
- `CompanyAgentRuntime(generation_llm: LLMClient | None = None)` → `semantic_dialogue.reply_to_requests(generation_llm=)`
  → `grounded_generation.generate_answers(llm=generation_llm or llm)`; planlayıcı ve kanıt kararları `customer`'da kalır.
- Audit: `job.audit["models"] = {"customer": {...}, "generation": {...}}`; `model`/`provider` anahtarları korunur.
- Preflight: rol uçları tekilleştirilerek `/v1/models` kontrolü.
- Ayarlar: `LLM_SCHEMA_MODE`, `LLM_ROLE_<GENERATION|EXTRACTION|MEMORY|ADMIN>_<PROVIDER|MODEL|BASE_URL|API_KEY|SCHEMA_MODE|NUM_CTX>`.
  Veri sınırı kapısı rol uçlarını da denetler (müşteri/tenant verisi işleyen tüm roller).

Kabul: `tests/test_llm.py` (rol fallback/override, `nvext` gövdesi respx ile), `test_grounded_generation.py`
("generation istemcisi yalnız üretim çağrısında"). Ölçüm: `experiments/nim/role_llm_eval.py`:
(a) çıkarım Qwen3 8B vs Nemotron 3.5 Lightning 30B-A3B — sunucu doğrulamasından geçen aday oranı, literal
alıntı oranı, **Türkçe çıktı oranı** (dil tespiti), gecikme; (b) hafıza — `docs/evaluation/turkish-dialogue-gold-corpus-v1.yaml`
diyaloglarından `subject_ids`/`intent` doğruluğu. Karar kuralı: hafıza rolü Nemotron'a geçer; çıkarım rolü
yalnız Türkçe çıktı ≥ %98 ve aday kalitesi Qwen3'ten düşük değilse geçer. Rapor: `experiments/nim/role-llm-report.json`.

## WP6 — İsteğe bağlı (WP1–WP5 kabul edildikten sonra, ayrı onayla)

**6a Ses notu → metin.** Migration `ALTER TYPE message_type ADD VALUE IF NOT EXISTS 'audio'` (`op.get_context().autocommit_block()`),
`MessageType.AUDIO`; webhook `type=="audio"` → `body=None`, `raw=m`, job oluşur (job oluşturma `body`'ye
bağlıysa audio için de oluştur); `WhatsAppClient.download_media(media_id) -> tuple[bytes, str]`
(Graph `GET /{ver}/{media_id}` → `url` → bearer GET; `ASR_MAX_AUDIO_BYTES`); `outreach/transcription.py`
`SpeechTranscriber` portu + `integrations/nim/asr.py` (Riva ASR HTTP, OpenAI uyumlu `/v1/audio/transcriptions` —
OpenAPI'den sabitle); worker'da "no text body" dalında `AUDIO` + `ASR_ENABLED` → indir → transkript →
`inbound.body = text[:4000]`, `job.audit["transcription"]` (metin yok) → normal akış (guardrail transkripti görür);
transkript `_OPT_OUT_RE` ile eşleşirse webhook opt-out yolu (yardımcıya ayır); boş transkript →
`clarification_text` varsa `ASK_CLARIFICATION`, yoksa `SKIPPED`. Ses baytı **hiçbir yerde saklanmaz**.
Testler: respx ASR, Postgres'li worker testi, opt-out transkripti. Ölçüm: 20 Türkçe ses notu → WER ≤ %15.
`docs/compliance.md`'ye "sesli mesajlar metne dönüştürülür, ses saklanmaz" satırı.

**6b Onaylı fact çevirisi (aday).** `modules/knowledge/translation.py` `Translator` portu + `propose_translations`
(`KnowledgeCandidate(kind="translation", payload={"fact_id","locale","customer_text"}, evidence={"source_locale","source_text_hash"},
protected = fact.protected or text_anchors(src) != text_anchors(dst))`); publisher `ConfigPatcher.add_translation`
+ `accept_candidate` desteği; `agent.supported_locales` kontrolü; admin chat listesinde `[Çeviri · en]`;
`POST /api/v1/knowledge/agents/{agent_id}/translations {locale}` → `knowledge` kuyruğu. **Asla otomatik yayın yok.**
Model Downloadable NIM'de çalışır. Testler: aday üretimi, çapa uyuşmazlığı → protected, accept → LIVE
`customer_text[en]`. Ölçüm: 40 fact TR→EN, çapa koruma %100 zorunlu.

---

## Dur ve sor (yalnız bu noktalarda)

1. Bir NIM konteynerine erişemiyorsan ve API şeklini doğrulayamıyorsan: adaptörü "unverified" fixture ile
   yaz, testleri geçir, WP raporunda açıkça işaretle ve devam et; PR açıklamasında listele.
2. WP4 A/B sonucu bge-m3'ü geçmiyorsa: varsayılanı değiştirme, raporu ekle, ADR-002 notunu yaz, devam et.
3. WP5 Türkçe çıktı oranı eşiğin altındaysa: çıkarım rolünü Qwen3'te bırak, yalnız hafıza rolünü taşı.
4. `message_type` enum migration'ı üretimde kilit gerektirebilir; WP6a'ya başlamadan onay iste.
5. Guardrail golden set'te yanlış-blok > %2 ise üretim önerme; `GUARDRAIL_TOPIC_CONTROL_MODE=flag` ve
   kategori listesiyle yeniden ölç, sonucu raporla.

## Bitiş raporu (her WP için, Türkçe)

- Değişen dosyalar ve yeni ayarlar (env adı → varsayılan).
- Kabul testleri: komut ve sonuç (kaç test, süre).
- Ölçüm raporu yolu ve tablo (metrik, değer, eşik, geçti/kaldı).
- "Doğrulanacak" bırakılan API şekilleri.
- Değişmezlerin her birinin nasıl korunduğu (tek cümle).
- Sonraki WP için ön koşullar.
