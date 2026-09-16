# Rol bazlı LLM değerlendirme fixture'ları (WP5)

Sentetik, Artı Kasnak ürün alanından. Gerçek tenant dokümanı veya müşteri konuşması içermez.

- `docs/*.md` — çıkarım (extraction) rolü için kısa Türkçe dokümanlar; `role_llm_eval.py`
  her dokümanı `chunk_text` ile böler ve iki modelle `extract_chunk` çalıştırır.
- `memory_dialogues.json` — hafıza rolü için diyaloglar: `turns` (role/content) ve beklenen
  `subject_ids` / `intent`. Çıktı yalnız onaylı enum'lardan oluştuğu için dil duyarsızdır.

Karar kuralı (plan WP5): hafıza rolü Nemotron'a geçebilir; çıkarım rolü yalnız Türkçe çıktı
oranı ≥ %98 ve doğrulanan aday oranı Qwen3'ten düşük değilse geçer.
