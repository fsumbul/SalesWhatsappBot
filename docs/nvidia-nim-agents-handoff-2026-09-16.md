# NVIDIA NIM ajanları — devir notu (2026-09-16)

Bu not, `feat/graphrag-self-service-knowledge` dalındaki (commit `8ee9fb8`) mimarinin üzerine
build.nvidia.com kataloğundan hangi modellerin hangi rolde ekleneceğini planlayacak yeni oturum
için hazırlandı. Kod okumadan önce şunlar bilinmeli:

## Mevcut durum (ADR-002 + ADR-003, uygulanmış ve push'lanmış)

- Runtime: `apps/api/src/modules/agents/company_runtime.py`, `semantic_dialogue.py`,
  `grounded_generation.py`, `grounded_audit.py`. Model yalnızca fact ID seçer; hibrit modda
  açıklayıcı istekler denetimli üretilir. Tek LLM fabrikası: `src/integrations/llm.py::get_llm_client()`
  (`LLM_PROVIDER=ollama|chat_compatible`, OpenAI uyumlu `/chat/completions`, `response_format json_schema`).
- Retrieval: `src/modules/knowledge/retrieval.py` (FalkorDB `kb_<tenant>_<version>`), embedding portu
  `src/integrations/embeddings.py::EmbeddingClient` (yalnız Ollama bge-m3 uygulaması), reranker portu
  `src/integrations/reranker.py::Reranker` (sentence-transformers bge-reranker-v2-m3, süreç içi).
- Bilgi kaynakları: `src/modules/knowledge/{extract,crawler,media,extraction,evidence,ingest,publisher,router}.py`,
  worker `src/workers/knowledge.py` (`knowledge` kuyruğu). OCR yok (yalnız PDF metin katmanı),
  görsel doğrulama heuristik, enjeksiyon süzgeci regex.
- Güvenlik boşluğu: gelen müşteri mesajı ve yüklenen chunk'lar için model tabanlı guardrail yok
  (`docs/project-handoff.md` "Phase E safety review").
- Ayarlar tek yerde: `src/core/config.py` (`Settings`). Üretim boot kapısı `production_runtime_errors()`.
- Testler: `apps/api/tests/test_knowledge_*.py`, `test_grounded_generation.py`, `test_company_runtime_retrieval.py`;
  golden set `apps/api/config/knowledge_golden.arti_kasnak.json`; Türkçe diyalog korpusu `docs/evaluation/`.

## Kural: hangi model nerede çalışabilir

- build.nvidia.com "Free Endpoint" deneme API'si girdileri kaydeder (site uyarısı: gizli/kişisel veri
  yüklemeyin). Repo ilkesi: müşteri mesajı buluta gitmez. Bu yüzden müşteri mesajı, tenant dokümanı ve
  görseli işleyen roller **Downloadable NIM konteyneri** olarak kendi Linux + NVIDIA GPU sunucusunda
  çalışmalı (`docker run --gpus all nvcr.io/nim/...`, OpenAI uyumlu `http://host:8000/v1`).
  Free endpoint yalnız sentetik veriyle deney ve değerlendirme için.
- NIM şema kısıtı için `response_format` yerine `nvext.guided_json` gerekebilir; `ChatCompletionsLLMClient`'a bayrak.

## Katalogdan seçilen modeller ve roller (98 model tarandı)

| Rol | Model(ler) | Deploy | Not |
|---|---|---|---|
| Girdi guardrail (müşteri mesajı, chunk, sayfa) | `nemotron-3.5-content-safety`, `llama-3.1-nemotron-safety-guard-8b-v3`, `nemoguard-jailbreak-detect`, `llama-3.1-nemoguard-8b-topic-control` | Downloadable | Çok dilli; Türkçe var |
| OCR / tablo / düzen (taranmış PDF) | `nemotron-ocr-v2`, `nemotron-page-elements-v3`, `nemotron-table-structure-v1`, `nemotron-graphic-elements-v1`, `nemotron-parse-2.0` (EN), `paddleocr` | Downloadable | NeMo Retriever çıkarım zinciri |
| Görsel doğrulama / alt metin | `llama-3.2-11b-vision-instruct`, `nemotron-3-nano-omni-30b-a3b-reasoning` | Downloadable | `media.py` subject/score kesinleştirme |
| Görsel-doküman retrieval | `llama-nemotron-embed-vl-1b-v2`, `llama-nemotron-rerank-vl-1b-v2` | Downloadable | Katalog sayfası görsel olarak |
| Embedding alternatifi | `nemotron-3-embed-1b` (2048d, kesilebilir 1024/512, 32k, `input_type=query/passage`) | Endpoint (+NGC) | Değerlendirme dillerinde Türkçe yok → bge-m3'e karşı golden set A/B şart |
| Arka plan ajanları (çıkarım, hafıza, denetleyici) | `nemotron-3.5-lightning-30b-a3b` (3B aktif, 1M ctx, tool-calling, structured output), `nemotron-3-super-120b-a12b` | Downloadable + endpoint | Resmî diller EN/ES/FR/DE/IT/JA; Türkçe müşteri metni üretimi için değil |
| Türkçe müşteri cevabı (hibrit üretim) | Yerel Qwen3 (mevcut) veya `gemma-4-31b-it` | Downloadable | Türkçe korpusla karşılaştır |
| Ses | `whisper-large-v3` (ASR, Türkçe), `chatterbox-multilingual-tts` / `magpie-tts-multilingual` | Downloadable | WhatsApp ses notu |
| Çeviri (TR/EN/DE/AR/RU) | `riva-translate-4b-instruct-v2` (37 dil) | Endpoint | Onaylı fact çevirisi aday olarak, admin onayı |
| Genel LLM (deney) | `glm-5-375`, `deepseek-v4-flash-0731`, `kimi-k3`, `gpt-oss-20b`, `mistral-nemotron` | Endpoint | Müşteri verisiyle kullanılmaz |

İşe yaramayanlar: biyoloji/ilaç, Cosmos/otonom araç, 3D/video, görsel üretim, cuOpt, fourcastnet.

## Entegrasyon noktaları

1. Rol bazlı model seçimi: bugün tek `get_llm_client()`; `LLM_ROLE_*` ayarlarıyla müşteri cevabı → yerel,
   çıkarım/denetim → NIM. `LLMClient` protokolü değişmemeli.
2. `EmbeddingClient` için NIM `/v1/embeddings` (+`input_type`) adaptörü; boyut değişince `kb_*`/`kn_*`
   parmak iziyle yeniden kurulur (`indexer.py`, `evidence.py`).
3. `Reranker` için NIM `/v1/ranking` adaptörü.
4. Guardrail kapısı: `src/workers/agent_runtime.py::_execute_runtime_job` model çağrısından önce;
   `src/modules/knowledge/ingest.py` chunk işlenmeden önce; sonuç `job.audit`'e.
5. OCR dalı: `extract.py::_extract_pdf` metin katmanı boşsa NIM OCR zinciri; locator `dosya.pdf#page=N&bbox=`.
6. Vision doğrulama: `media.py::discover_images` sonrası, `KnowledgeMedia.subject_id/score` güncellemesi.
7. Ses: webhook `audio` tipi → ASR → mevcut inbound metin yolu.

Öncelik: (1) guardrail, (2) OCR zinciri, (3) vision doğrulama, (4) embedding/rerank A/B, (5) rol bazlı LLM.
