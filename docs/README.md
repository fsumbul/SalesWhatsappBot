# Documentation

## Amaç yönelimli diyalog araştırma ve spesifikasyon paketi

- [`intentional-dialogue-architecture-research-2026-08-03.md`](./intentional-dialogue-architecture-research-2026-08-03.md) — araştırma sentezi ve ana mimari karar
- [`dialogue-behavior-ontology-v1.md`](./dialogue-behavior-ontology-v1.md) — normatif davranış ontolojisi
- [`dialogue-state-transition-repair-spec-v1.md`](./dialogue-state-transition-repair-spec-v1.md) — state-transition, belirsizlik ve repair sözleşmesi
- [`turkish-dialogue-gold-corpus-annotation-guide-v1.md`](./turkish-dialogue-gold-corpus-annotation-guide-v1.md) — Türkçe corpus annotation ve kalite rehberi
- [`evaluation/turkish-dialogue-gold-corpus-v1.yaml`](./evaluation/turkish-dialogue-gold-corpus-v1.yaml) — 55 senaryolu altın değerlendirme corpus'u
- [`adr/ADR-001-intentional-dialogue-runtime.md`](./adr/ADR-001-intentional-dialogue-runtime.md) — framework karşılaştırması ve runtime ADR'si
- [`adr/ADR-002-falkordb-graphrag-retrieval.md`](./adr/ADR-002-falkordb-graphrag-retrieval.md) — FalkorDB üzerinde hibrit GraphRAG retrieval (BGE-M3 + Türkçe full-text + graf + reranker) ve müşteri hafıza grafiği
- [`adr/ADR-003-self-service-knowledge-and-hybrid-answers.md`](./adr/ADR-003-self-service-knowledge-and-hybrid-answers.md) — tenant'ın kendi doküman/web sitesi/görsellerini yüklemesi, otomatik + geri alınabilir yayın, hibrit (literal + denetimli üretim) cevap modu
- [`multi-tenant-vector-retrieval-architecture.md`](./multi-tenant-vector-retrieval-architecture.md) — retrieval güven sınırları ve kabul testleri (ADR-002 ile uygulandı)

Bu paket ürün kodundan önce okunmalıdır. Ana araştırma gerekçeyi, ontoloji ve transition belgeleri normatif davranışı, corpus kabul koşullarını, ADR ise uygulama yaklaşımını tanımlar.

## Genel proje belgeleri

- `PROJECT_PLAN.md` (root) — high-level project plan and phases
- `BUILD_PROMPT.md` (root) — AI prompt for phased implementation
- Later phases will add:
  - `architecture.md` — Mermaid diagrams and module boundaries
  - `api.md` — generated OpenAPI reference
  - `compliance.md` — KVKK/GDPR checklist and runbook
  - `runbook.md` — production ops and incident response
