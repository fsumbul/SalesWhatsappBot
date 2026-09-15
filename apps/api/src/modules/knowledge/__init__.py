"""Knowledge retrieval and conversation memory (GraphRAG, ADR-002).

The company JSON stays the source of truth. This package derives a FalkorDB
graph + vector + full-text index from *approved, customer-visible* facts,
retrieves ranked candidates for the trusted runtime, and keeps a per-customer
memory graph that is decision input only.
"""
