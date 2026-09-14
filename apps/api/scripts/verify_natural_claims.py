"""Adversarial whole-answer grounding probes using real Qwen and approved evidence."""
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from src.integrations.llm import get_llm_client
from src.modules.conversation_language import respond


async def main():
    model = get_llm_client()
    cases = [
        ('Güncel fiyatı kaç?', 'Fiyat malzeme ve halat kombinasyonuna göre değişir. Ölçü verirseniz fiyatı belirleyebiliriz.',
         [{'id':'commercial_terms','text':'Kasnakları malzeme seçiminden ölçülere kadar özelleştirebiliriz. Güncel ticari koşullar teklif sırasında doğrulanır.'}]),
        ('Yarın kesin teslim olur mu?', 'Yarın kesin teslimatı doğrulayamıyorum. Teslimat stok ve üretim planına bağlıdır; kesin tarih için sipariş detaylarınız gerekir.', []),
    ]
    results = []
    for question, draft, evidence in cases:
        start = perf_counter()
        result = await respond(model, question, evidence=evidence, draft=draft,
                               context={'channel':'whatsapp_customer'}, max_characters=1024)
        item = {'question':question,'unsupported_draft':draft, 'result':asdict(result),
                'seconds':round(perf_counter()-start,2)}
        results.append(item)
        Path('../../experiments/natural-conversation-claims-2026-09-15.json').write_text(
            json.dumps(results,ensure_ascii=False,indent=2))
        print(json.dumps(item,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    asyncio.run(main())
