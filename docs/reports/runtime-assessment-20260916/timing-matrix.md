# Test × aşama süre matrisi — 16 Eylül 2026

Bütün süreler saniyedir. 16 senaryo × 3 tekrar = 48 ölçüm. Yeni test koşulmadı; mevcut JSON ölçümleri tabloya dönüştürüldü. İki düzeltilmiş senaryoda model-corrected.json kullanıldı; diğer satırlar model-acceptance.json kaynaklıdır.

Embedding aramanın içindedir; ayrıca toplama eklenmez. Üretim süresi denetim ve literal yanıta dönüşü de içerir. İç içe span süreleri toplanmaz. “—” aşamanın ölçülmediğini belirtir. 0.000 yuvarlamadır. Kuyruk, Windows tüneli ve WhatsApp gönderim/teslim süreleri dahil değildir.

| Senaryo | Tekrar | Niyet LLM | Arama | ↳ Embedding | Kanıt kararı LLM | Üretim | Toplam | Kabul | Üretim durumu | Yanıt kaynağı |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| 6211 rulman hangi mil çapına uygundur? | 1 | 13.652 | 1.815 | 1.812 | 12.034 | 12.006 | 39.508 | Geçti | failed:TimeoutError | literal |
| TS180118 kaç halat için? | 1 | 14.947 | 0.000 | — | 6.653 | 0.000 | 21.602 | Başarısız | not_applicable | literal |
| Palanga kasnağının malzemesi ve çalışma özellikleri nedir? | 1 | 9.237 | 0.106 | 0.102 | 15.575 | 12.015 | 36.934 | Geçti | failed:TimeoutError | literal |
| Captormal kasnakların standart çap seçenekleri nelerdir? | 1 | 11.166 | 0.141 | 0.138 | 12.958 | 12.015 | 36.282 | Geçti | failed:TimeoutError | literal |
| Döküm kasnakta 320 mm çap seçeneği var mı? | 1 | 17.046 | 2.935 | 2.932 | 15.205 | 12.014 | 47.201 | Geçti | failed:TimeoutError | literal |
| Yan desteklerde hangi mil çapları var? | 1 | 14.404 | 0.125 | 0.115 | 20.940 | 12.014 | 47.484 | Geçti | failed:TimeoutError | literal |
| Özel ölçü üretim yapıyor musunuz? | 1 | 17.438 | 0.000 | — | 7.524 | 0.000 | 24.963 | Başarısız | not_applicable | literal |
| İhracat ekibine nasıl ulaşabilirim? | 1 | 16.244 | 1.147 | 1.143 | 14.778 | 0.000 | 32.169 | Geçti | not_applicable | literal |
| kasnaklarınız sessiz mi çalışıyo | 1 | 17.184 | 1.158 | 1.154 | 17.415 | 0.000 | 35.758 | Başarısız | not_applicable | literal |
| halatın yönünü değiştiren parça hakkında bilgi | 1 | 19.267 | 0.173 | 0.170 | 22.324 | 12.015 | 53.780 | Geçti | failed:TimeoutError | literal |
| ölçüleri neler | 1 | 6.984 | 0.102 | 0.098 | 8.218 | 7.807 | 23.112 | Geçti | ok | literal |
| kaç ülkeye satıyorsunuz | 1 | 6.102 | 0.068 | 0.065 | 4.387 | 7.864 | 18.421 | Geçti | ok | generated |
| guided_menu | 1 | — | — | — | — | — | 0.000 | Geçti | not_run | literal |
| stale_history_menu | 1 | — | — | — | — | — | 0.000 | Geçti | not_run | literal |
| descriptive_material | 1 | 10.577 | 2.336 | 2.333 | 20.890 | 12.013 | 45.818 | Geçti | failed:TimeoutError | literal |
| unknown_price_stock | 1 | 13.812 | 0.160 | 0.242 | 7.528 | 0.000 | 21.501 | Geçti | not_applicable | literal |
| TS180118 kaç halat için? | 2 | 10.504 | 0.000 | — | 5.366 | 0.000 | 15.871 | Başarısız | not_applicable | literal |
| Palanga kasnağının malzemesi ve çalışma özellikleri nedir? | 2 | 8.603 | 0.098 | 0.095 | 11.535 | 12.014 | 32.251 | Geçti | failed:TimeoutError | literal |
| Captormal kasnakların standart çap seçenekleri nelerdir? | 2 | 8.639 | 0.096 | 0.093 | 9.565 | 12.015 | 30.316 | Geçti | failed:TimeoutError | literal |
| Döküm kasnakta 320 mm çap seçeneği var mı? | 2 | 9.196 | 0.118 | 0.116 | 8.265 | 9.707 | 27.288 | Geçti | ok | generated |
| Yan desteklerde hangi mil çapları var? | 2 | 7.198 | 0.124 | 0.121 | 7.192 | 10.009 | 24.523 | Geçti | ok | generated |
| Özel ölçü üretim yapıyor musunuz? | 2 | 6.253 | 0.000 | — | 4.587 | 0.000 | 10.841 | Başarısız | not_applicable | literal |
| İhracat ekibine nasıl ulaşabilirim? | 2 | 6.447 | 0.103 | 0.100 | 6.096 | 0.000 | 12.647 | Geçti | not_applicable | literal |
| kasnaklarınız sessiz mi çalışıyo | 2 | 6.359 | 0.078 | 0.075 | 6.347 | 0.000 | 12.784 | Başarısız | not_applicable | literal |
| halatın yönünü değiştiren parça hakkında bilgi | 2 | 7.110 | 0.096 | 0.093 | 7.829 | 12.012 | 27.047 | Geçti | failed:TimeoutError | literal |
| ölçüleri neler | 2 | 8.026 | 0.107 | 0.103 | 5.948 | 7.814 | 21.894 | Geçti | ok | literal |
| kaç ülkeye satıyorsunuz | 2 | 6.098 | 0.075 | 0.072 | 4.383 | 7.860 | 18.416 | Geçti | ok | generated |
| guided_menu | 2 | — | — | — | — | — | 0.000 | Geçti | not_run | literal |
| stale_history_menu | 2 | — | — | — | — | — | 0.000 | Geçti | not_run | literal |
| descriptive_material | 2 | 6.914 | 0.123 | 0.119 | 7.972 | 9.530 | 24.540 | Geçti | ok | generated |
| unknown_price_stock | 2 | 10.294 | 0.120 | 0.199 | 7.757 | 0.000 | 18.172 | Geçti | not_applicable | literal |
| 6211 rulman hangi mil çapına uygundur? | 2 | 6.603 | 0.110 | 0.107 | 7.299 | 8.027 | 22.040 | Geçti | ok | generated |
| Palanga kasnağının malzemesi ve çalışma özellikleri nedir? | 3 | 7.059 | 0.103 | 0.099 | 8.192 | 12.012 | 27.367 | Geçti | failed:TimeoutError | literal |
| Captormal kasnakların standart çap seçenekleri nelerdir? | 3 | 7.171 | 0.114 | 0.111 | 7.856 | 12.013 | 27.156 | Geçti | failed:TimeoutError | literal |
| Döküm kasnakta 320 mm çap seçeneği var mı? | 3 | 7.495 | 0.126 | 0.122 | 8.365 | 9.804 | 25.791 | Geçti | ok | generated |
| Yan desteklerde hangi mil çapları var? | 3 | 6.467 | 0.091 | 0.088 | 7.290 | 10.069 | 23.917 | Geçti | ok | generated |
| Özel ölçü üretim yapıyor musunuz? | 3 | 6.279 | 0.000 | — | 4.618 | 0.000 | 10.898 | Başarısız | not_applicable | literal |
| İhracat ekibine nasıl ulaşabilirim? | 3 | 6.425 | 0.084 | 0.081 | 6.149 | 0.000 | 12.659 | Geçti | not_applicable | literal |
| kasnaklarınız sessiz mi çalışıyo | 3 | 6.334 | 0.084 | 0.081 | 6.297 | 0.000 | 12.716 | Başarısız | not_applicable | literal |
| halatın yönünü değiştiren parça hakkında bilgi | 3 | 6.802 | 0.090 | 0.087 | 7.838 | 12.011 | 26.743 | Geçti | failed:TimeoutError | literal |
| ölçüleri neler | 3 | 7.030 | 0.106 | 0.103 | 5.949 | 7.827 | 20.912 | Geçti | ok | literal |
| kaç ülkeye satıyorsunuz | 3 | 6.082 | 0.063 | 0.060 | 4.422 | 7.863 | 18.431 | Geçti | ok | generated |
| guided_menu | 3 | — | — | — | — | — | 0.001 | Geçti | not_run | literal |
| stale_history_menu | 3 | — | — | — | — | — | 0.000 | Geçti | not_run | literal |
| descriptive_material | 3 | 6.858 | 0.136 | 0.133 | 7.916 | 9.484 | 24.395 | Geçti | ok | generated |
| unknown_price_stock | 3 | 10.299 | 0.122 | 0.203 | 7.717 | 0.000 | 18.139 | Geçti | not_applicable | literal |
| 6211 rulman hangi mil çapına uygundur? | 3 | 6.619 | 0.113 | 0.110 | 7.311 | 7.969 | 22.013 | Geçti | ok | generated |
| TS180118 kaç halat için? | 3 | 6.427 | 0.000 | — | 4.618 | 0.000 | 11.045 | Başarısız | not_applicable | literal |
