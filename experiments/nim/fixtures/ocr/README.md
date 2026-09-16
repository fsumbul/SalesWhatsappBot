# OCR değerlendirme fixture'ları (WP2)

Buraya **sentetik veya izinli** taranmış Türkçe sayfalar konur; gerçek müşteri/tenant
dokümanı konmaz. Her örnek için:

- `<ad>.png` / `<ad>.jpg` / `<ad>.pdf` — sayfa görüntüsü (PDF ise her sayfa ayrı örnek sayılır),
- `<ad>.txt` — elle yazılmış ground truth metin (okuma sırasıyla, satır satır),
- isteğe bağlı `<ad>.table.json` — `[["Çap","Halat"],["320 mm","4x8"], ...]` biçiminde
  beklenen tablo hücreleri (yalnız ilk tablo).

`ocr_eval.py` her örnek için CER/WER, tablo hücre doğruluğu ve sayfa süresini raporlar.
Hedef: en az 10 sayfa (katalog, fiyat listesi, teknik föy); kabul CER ≤ %5, hücre ≥ %90.
