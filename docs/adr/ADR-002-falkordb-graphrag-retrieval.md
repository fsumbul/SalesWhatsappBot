# ADR-002: FalkorDB Üzerinde GraphRAG Retrieval ve Müşteri Hafıza Grafiği

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-15
- **Kapsam:** WhatsApp müşteri ajanı runtime'ı (`company_runtime`, `semantic_dialogue`, `agent_runtime` worker'ı), bilgi indeksleme, arka plan hafıza zenginleştirme
- **İlgili belgeler:** [Çok şirketli vector retrieval mimarisi](../multi-tenant-vector-retrieval-architecture.md) (güven sınırları ve kabul testleri bu ADR'de aynen geçerlidir), [ADR-001](ADR-001-intentional-dialogue-runtime.md), [Company agent config](../company-agent-config.md)

## 1. Karar

Onaylı `CompanyAgentConfig` grafiği, **FalkorDB** üzerinde tenant + agent sürümü başına bir grafiğe türetilir ve müşteri sorusu için aday fact'ler **hibrit retrieval** ile bulunur:

| Kanal | Ne yapar | Örnek |
|---|---|---|
| `exact` | Ürün/rulman kodları ve ölçüler `(:Code)-[:CODE_OF]->(:Fact)` üzerinden kesin eşleşir, sonuçta **her zaman en üste sabitlenir** | `6211`, `TS180118`, `320 mm` |
| `vector` | **BGE-M3** (Ollama, 1024 boyut, cosine, HNSW) anlamsal benzerlik | "halatın yönünü değiştiren parça" → `cast_pulley_role_and_types` |
| `fulltext` | RediSearch **Türkçe kök bulma** ile tam metin | "kasnaklarınız sessiz mi çalışıyo" → `plastic_pulley_performance` |
| `graph` | Güvenilir konu çapaları (önceki bot turu, mesajdaki ürün adı) → `[:ABOUT]` ve `is_variant_of`/`part_of` soy zinciri (1–3 sıçrama) | `deflection_pulley` → `cast_elevator_pulley` fact'leri |
| `memory` | Aynı soy zinciri yürüyüşü, müşteri hafıza grafiğindeki ürünler için (daha düşük ağırlık) | önceki konuşmada `belt_pulley` sorulmuşsa kayış fact'leri |

Kanallar **Reciprocal Rank Fusion** ile birleşir, ardından **`BAAI/bge-reranker-v2-m3`** cross-encoder (sentence-transformers, Apple MPS/CUDA/CPU) en iyi 24 adayı yeniden sıralar; güvenilir çapa soy zincirindeki fact'ler sabit bir bonus (`graph` +0,20, `memory` +0,10 rerank ölçeğinde) alır ki graf yerelliği füzyon ve rerank sonrasında da korunsun. Sonuç yalnızca bir **aday listesidir**: model (Qwen3 8B, Ollama, düşünme kapalı) hâlâ sadece bu listeden fact ID seçer, sunucu literal `customer_text` render eder. Retrieval hiçbir zaman müşteri metni üretmez.

Her müşteri turu tamamlandıktan sonra arka planda (`knowledge` Celery kuyruğu) model, konuşmayı **onaylı sözlükle kısıtlanmış** yapısal bir özete çevirir (hangi ürünler, hangi teklif alanları, niyet, duygu) ve bu `mem_<tenant>` grafiğine yazılır. Hafıza yalnızca **karar girdisidir**: retrieval'ı müşterinin ürünlerine çapalar ve planlayıcıya `customer_memory` olarak gösterilir; asla müşteriye render edilmez.

## 2. Bağlam ve neden pgvector değil

`docs/multi-tenant-vector-retrieval-architecture.md` ilk seçenek olarak pgvector'ü önermişti ve backend'in `FactRetriever` portunun arkasında değişebileceğini açıkça belirtmişti. FalkorDB tercih edildi çünkü:

1. **Şirket bilgisi zaten bir graftır.** `offerings`, `relationships` (`is_variant_of`, `part_of`, `offers`) ve `facts` doğrudan düğüm/kenar olur; `_applicable_fact_subject_ids` ile elde edilen soy zinciri tek bir Cypher path sorgusudur (`[:REL*1..3]`). pgvector'da bu ayrı bir SQL recursive CTE + join katmanı gerektirir.
2. **Tek motor.** Vektör (HNSW), tam metin (RediSearch, Türkçe stemmer) ve graf traversal aynı süreçte, aynı sorgu dilinde; ayrı bir vektör veritabanı veya Postgres uzantısı yönetilmez. Postgres yalnızca gerçeklik kaynağı ve RLS'li operasyonel veri olarak kalır.
3. **Yapısal izolasyon.** Graf adı `kb_<tenant_hex>_<version_hex>`; sorgu, sunucunun çözdüğü kapsam dışındaki hiçbir grafiğe dokunamaz. Yeni LIVE sürüm = yeni graf (atomik), eski graf rollback için kalır.
4. **Müşteri hafızası doğal olarak graftır.** `(:Customer)-[:INTERESTED_IN {count,last_seen}]->(:Subject)`, `(:Customer)-[:STATED]->(:Requirement)` sorguları ve zamanla büyüyen ilişkiler için graf modeli uygundur.

Ölçülen retrieval kalitesi (Artı Kasnak, 63 onaylı fact, `apps/api/config/knowledge_golden.arti_kasnak.json`, reranker açık): **recall@1 0,92 · recall@3 1,00 · MRR 0,96**; kod içeren sorularda (`6211`, `TS180118`, `320 mm`) doğru fact her zaman 1. sırada. `make knowledge-search` ile yeniden ölçülür.

## 3. Güven sınırları (değişmedi)

- Yalnızca `customer_visible = true` ve `customer_text` olan fact'ler indekslenir. `Fact.value`, `source`, müşteri profili tanımları ve modül verisi indekse, embedding'e, prompt'a girmez (`compiler.compile_search_document`).
- Tenant ve sürüm webhook/DB'den çözülür; retriever `ScopedFactRetriever` ile bağlanır, runtime graf adını ve tenant'ı bilmez.
- Retriever **kapsamı genişletemez**: `company_runtime._retrieved_selection` mesajda açıkça adı geçen ürünün soy zinciri + şirket fact'leri dışına çıkmaz ve ürünün kendi fact'lerini öne alır; `semantic_dialogue.request_candidates` yalnızca soy zinciri + kategori kapılarından geçen fact'leri yeniden sıralar. Fiyat/stok/teslimat/garanti/sertifika/uygunluk korumaları retrieval'dan **sonra** aynen çalışır.
- Prompt, şema `enum`'u ve parser aynı aday listesini paylaşır (tur başına tek retrieval).
- Retrieval kullanılamıyorsa (indeks yok, FalkorDB/Ollama kapalı, zaman aşımı 4 sn) runtime **sessizce sözcüksel seçiciye** döner ve `AgentRuntimeJob.audit.retrieval.status = fallback_lexical` yazar. Retrieval hatası hiçbir zaman handoff üretmez, hiçbir zaman tüm fact deposunu prompt'a koymaz.
- Hafıza grafiğinde ham mesaj, telefon numarası veya serbest metin **yoktur**: müşteri anahtarı `HMAC(app_secret, tenant_id, telefon)`; ürünler onaylı `offering.id`, alanlar `customer_profiles[].fields[].id` enum'larıdır; yalnızca gereksinim değeri (ör. `320`) müşterinin kendi kısa ifadesidir. `ConversationMemoryStore.forget` veri sahibi silme talebi içindir.
- Ham dokümanlar (PDF/Markdown) `scripts/ingest_documents.py` ile yalnızca **aday** fact üretir (`cand_<tenant>` staging grafiği + JSON + `customer_visible: false` draft config). Yönetici onayı olmadan runtime indeksine girmez.

## 4. Kod haritası

```
apps/api/src/integrations/embeddings.py      EmbeddingClient portu, OllamaEmbeddingClient (bge-m3)
apps/api/src/integrations/reranker.py        Reranker portu, CrossEncoderReranker (bge-reranker-v2-m3)
apps/api/src/modules/knowledge/ports.py      RetrievalRequest/Result, FactRetriever, KnowledgeRetriever
apps/api/src/modules/knowledge/compiler.py   search document, kod çıkarımı, Türkçe FTS sorgusu
apps/api/src/modules/knowledge/graph_store.py FalkorDB sarmalayıcı, graf adlandırma
apps/api/src/modules/knowledge/indexer.py    config → graf (idempotent, fingerprint)
apps/api/src/modules/knowledge/retrieval.py  FalkorGraphFactRetriever (4 kanal + RRF + rerank), ScopedFactRetriever
apps/api/src/modules/knowledge/memory.py     ConversationMemoryStore, extract_memory (kısıtlı şema)
apps/api/src/modules/knowledge/service.py    Settings → port fabrikaları
apps/api/src/workers/knowledge.py            Celery: index_agent_version, enrich_conversation_memory, reranker ısıtma
apps/api/scripts/knowledge_index.py          LIVE sürüm indeksleme CLI
apps/api/scripts/knowledge_search.py         retrieval sorgu/golden değerlendirme CLI
apps/api/scripts/ingest_documents.py         PDF/MD → aday fact (Knowledge Ops)
```

Runtime dokunuşları: `CompanyAgentRuntime(fact_retriever=, customer_memory=)`, `_visible_facts(candidate_fact_ids=)`, `reply_to_requests(fact_retriever=, customer_memory=)`, `RuntimeTurn.retrieval`, worker'da `_knowledge_inputs` ve gönderim sonrası `_enqueue_memory_enrichment`, `AgentService.promote_to_live/rollback_to` → `index_agent_version.delay`.

## 5. Gecikme bütçesi (M5 Max, yerel Ollama, ölçülen)

| Aşama | Süre |
|---|---|
| bge-m3 sorgu embedding | ~130 ms |
| FalkorDB exact + fulltext + graph | 1–3 ms |
| bge-reranker-v2-m3 (24 çift, MPS) | ~200 ms (ilk yükleme ~6 sn; worker başlangıcında ısıtılır) |
| **Toplam retrieval (istek başına)** | **300–400 ms** |
| Qwen3 8B karar çağrıları (planlayıcı + kanıt) | 1–3 sn |
| İndeks kurulumu (63 fact) | ~2 sn; değişmemişse 1 ms (fingerprint) |

## 6. Ayarlar

`KNOWLEDGE_BACKEND=falkordb`, `EMBEDDING_PROVIDER=ollama` (zorunlu çift; üretim boot kapısı kontrol eder), `FALKORDB_HOST/PORT`, `EMBEDDING_MODEL=bge-m3`, `EMBEDDING_DIMENSION=1024`, `RERANKER_ENABLED`, `RERANKER_MODEL`, `RERANKER_DEVICE`, `RETRIEVAL_MAX_CANDIDATES=12`, `RETRIEVAL_RERANK_POOL=24`, `RETRIEVAL_TIMEOUT_SECONDS=4`, `MEMORY_ENRICHMENT_ENABLED`. Varsayılan `lexical`: hiçbir ek servis olmadan eski davranış.

## 7. Sonuçlar ve açık işler

- Embedding modeli değişince fingerprint değişir ve indeks yeniden kurulur; farklı modellerin vektörleri asla aynı grafta karışmaz.
- Reranker süreç içi çalışır (RAM ~2 GB); GPU'suz sunucuda `RERANKER_ENABLED=false` ile kapatılabilir, RRF sırası kullanılır.
- Hafıza zenginleştirme `.delay()` ile en iyi çaba; broker kapalıysa tur kaybolmaz ama o turun hafızası yazılmaz (hafıza doğruluk değil kişiselleştirme girdisidir). Kalıcı iş satırı + tarama gerekirse `AgentRuntimeJob` deseni kopyalanır.
- Hafıza saklama süresi ve otomatik silme politikası deployment kararıdır (`forget` hazır).
- Aday fact inceleme arayüzü (staging grafiği → draft patch) henüz admin panelde yok; CLI çıktısı draft config olarak verilir.
