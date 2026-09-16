@echo off
rem Private SSH forwards terminate on loopback; production .env stays unchanged.
set "KNOWLEDGE_BACKEND=falkordb"
set "FALKORDB_HOST=127.0.0.1"
set "FALKORDB_PORT=6381"
set "EMBEDDING_PROVIDER=ollama"
set "EMBEDDING_BASE_URL=http://127.0.0.1:11439"
set "EMBEDDING_MODEL=bge-m3"
set "EMBEDDING_DIMENSION=1024"
set "RERANKER_ENABLED=false"
set "MEMORY_ENRICHMENT_ENABLED=true"
set "KNOWLEDGE_PUBLIC_MEDIA_BASE_URL=https://api.ashiraai.com"
set "HYBRID_GENERATION_ENABLED=true"
