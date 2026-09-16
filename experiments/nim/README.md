# NIM harness ajanları — değerlendirme deneyleri

Bu klasör, `docs/nvidia-nim-harness-agents-plan-2026-09-16.md` iş paketlerinin ölçüm
scriptlerini ve raporlarını tutar. Her script bir JSON rapor yazar (`<wp>-report.json`);
rapor olmadan ilgili iş paketi bitmiş sayılmaz.

## Veri kuralı

- Scriptler varsayılan olarak **yalnız özel ağdaki (Downloadable) NIM** uçlarına bağlanır.
  `--base-url` denylist'teki bir host'a (`integrate.api.nvidia.com` vb.) işaret ediyorsa script
  durur. `--allow-cloud` yalnız **sentetik** girdiyle (bu klasördeki fixture'lar) kullanılabilir;
  gerçek müşteri mesajı, tenant dokümanı veya görseli hiçbir zaman bulut ucuna gönderilmez.
- Raporlar metin içerebilir (fixture'lar sentetiktir); yine de raporlara müşteri verisi koymayın.

## Scriptler

| Script | İş paketi | Girdi | Rapor |
|---|---|---|---|
| `guardrail_eval.py` | WP1 | `apps/api/config/guardrail_golden.tr.json` | `guardrail-report.json` |
| `ocr_eval.py` | WP2 | `fixtures/ocr/` (sayfa görüntüsü + `*.txt` ground truth) | `ocr-report.json` |
| `vision_eval.py` | WP3 | `fixtures/vision/labels.json` | `vision-report.json` |
| `retrieval_ab.py` | WP4 | `apps/api/config/knowledge_golden.arti_kasnak.json` | `retrieval-ab-report.json` |
| `role_llm_eval.py` | WP5 | `fixtures/role_llm/` | `role-llm-report.json` |

Çalıştırma (`apps/api` içinden, uygulama `.env`'i ile):

```bash
poetry run python ../../experiments/nim/guardrail_eval.py --report ../../experiments/nim/guardrail-report.json
```

Ortak yardımcılar `_common.py` içindedir (rapor yazımı, yüzdelikler, host denetimi).
