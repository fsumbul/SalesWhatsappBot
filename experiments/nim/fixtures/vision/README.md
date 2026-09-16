# Görsel doğrulama fixture'ları (WP3)

`labels.json` biçimi:

```json
{
  "subject_labels": {"cast_pulley": "Döküm asansör kasnağı", "mc_nylon_pulley": "Captormal kasnak"},
  "items": [
    {"file": "cast_01.jpg", "expected_subject": "cast_pulley", "is_product_photo": true, "context": "Döküm kasnak ürün sayfası"},
    {"file": "logo.png", "expected_subject": null, "is_product_photo": false, "context": "Ana sayfa"}
  ]
}
```

Görseller **sentetik veya izinli** olmalıdır (tenant sitesinden alınmış gerçek görseller yalnız
tenant onayıyla ve yalnız özel ağdaki NIM ile). Hedef ≥ 30 görsel: ürün fotoğrafları (doğru ürün),
yanlış ürün, logo/ikon/banner/insan. `vision_eval.py` subject eşleşme precision/recall'u ve
yanlış-ret oranını raporlar; kabul precision ≥ %90, yanlış-ret ≤ %10.
