# ADR-004: NVIDIA NIM Harness Ajanları ve Veri Sınırı

- **Durum:** Taslak (WP0–WP5 uygulandı; GPU sunucusu ölçümleri bekleniyor)
- **Tarih:** 2026-09-16
- **Kapsam:** Guardrail kapısı, taranmış PDF için OCR/tablo zinciri, ürün görseli doğrulama, NIM embedding/rerank adaptörleri, rol bazlı LLM seçimi; bunların veri sınırı ve fail-closed davranışı
- **İlgili:** [ADR-002](ADR-002-falkordb-graphrag-retrieval.md), [ADR-003](ADR-003-self-service-knowledge-and-hybrid-answers.md), [plan](../nvidia-nim-harness-agents-plan-2026-09-16.md), [handoff](../nvidia-nim-agents-handoff-2026-09-16.md)

## 1. Karar

build.nvidia.com kataloğundan seçilen NIM modelleri mimariye **harness ajanı** olarak eklenir:
sınıflandırır, çıkarır, dönüştürür; **karar vermez ve müşteriye metin yazmaz**. "Model fact ID seçer,
sunucu literal metni render eder" sözleşmesi (ADR-001/002) ve ADR-003 denetim zinciri değişmez.

| Ajan | Girdi | Çıktı (yalnız veri) | Kanca |
|---|---|---|---|
| Guardrail (jailbreak + içerik güvenliği + konu kontrolü) | müşteri mesajı; chunk/OCR sayfası | `GuardVerdict` (allow/flag/block/unavailable) | worker'da model öncesi; ingest'te çıkarım öncesi |
| OCR/layout (page-elements → OCR; table-structure + OCR) | metin katmanı boş PDF sayfası | `TextUnit` (`dosya.pdf#page=N&bbox=…`) | `ingest._sync_documents` |
| Vision | keşfedilen ürün görseli | `VisionVerdict` → ret / override / alt metin | `ingest._ingest_images` |
| Embedding / rerank | fact ve chunk metni; sorgu | vektör; [0,1] skor | `EmbeddingClient` / `Reranker` portları |
| Rol LLM (extraction, memory, admin, generation) | mevcut şema kısıtlı istekler | aynı `LLMClient` çıktısı | `get_llm_client(role)` |

## 2. Veri sınırı (kodla zorlanır)

- Müşteri mesajı, tenant dokümanı/görseli yalnızca operatörün kendi GPU sunucusundaki Downloadable NIM
  konteynerine gider. `Settings.model_endpoint_boundary_errors()` üretimde public trial host'larını
  (`NIM_PUBLIC_HOST_DENYLIST`) her etkin uç için reddeder; `runtime_preflight.py --require-nim` hazır
  olmayan konteynerde çıkış kodu 1 verir.
- Guardrail üretimde `closed` modda çalışmak zorundadır: sınıflandırıcı erişilemezse model çağrılmaz,
  onaylı güvenli tur döner.
- Audit izleri (`AgentRuntimeJob.audit.guardrail`, `knowledge_chunks.guard`, `knowledge_media.verification`)
  etiket/skor/model/süre içerir; metin, görsel ve sağlayıcı yanıt gövdesi içermez.

## 3. Sonuçlar

- Bloklanan tur konuşmayı duraklatmaz (DECLINE); konu-dışı yalnız işaretlenir (varsayılan) çünkü
  NemoGuard modellerinin Türkçe desteği resmî değildir ve yanlış-blok satış kaybıdır.
- Embedding profili (`model#dimension`) her parmak izinin parçasıdır; `kb_*` kendini yeniden kurar,
  `kn_*` için `make knowledge-reembed` gerekir; uyuşmazlıkta ingest grafa yazmadan devam eder.
- `nemotron-3-embed-1b` yalnız 2048 boyut verir; handoff'taki "kesilebilir 1024/512" notu düzeltildi.
- NIM rerank logit'leri sigmoid ile [0,1]'e çekilir; ADR-003 entailment eşiği (0,30) NIM için
  `experiments/nim/retrieval_ab.py` ile yeniden kalibre edilir.

## 4. Açık işler

- GPU sunucusunda konteyner OpenAPI'siyle fixture doğrulaması (`tests/fixtures/nim/*.json` "UNVERIFIED").
- Ölçüm raporları: `experiments/nim/{guardrail,ocr,vision,retrieval-ab,role-llm}-report.json`.
- WP6 (ses notu → metin, onaylı fact çevirisi) ayrı onayla.
