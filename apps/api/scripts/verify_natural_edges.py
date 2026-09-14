"""Supplementary real-Qwen scope matrix and intake checks, local synthetic data only."""
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.engine import make_url

from src.core.config import get_settings
from src.core.db import dispose_engine, session_scope
from src.integrations.llm import LLMMessage, get_llm_client
from src.modules.admin_chat.workflow_inbox import read_messages
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.auth.models import Tenant, User
from src.modules.conversation_language import respond
from src.modules.selection.service import preview_natural

REPORT = Path('../../experiments/natural-conversation-qwen-edges-2026-09-15.json')


async def main():
    url = make_url(str(get_settings().database_url))
    if url.host not in {'127.0.0.1', 'localhost'} or url.database != 'leadpulse_test':
        raise SystemExit('Disposable local database required')
    scope_only = '--scope-only' in sys.argv
    report = json.loads(REPORT.read_text()) if scope_only and REPORT.exists() else {
        'scope': [], 'intake': [], 'customer_final': []}
    report['scope'] = []
    def record(channel, item):
        report[channel].append(item)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({channel: item}, ensure_ascii=False), flush=True)
    async with session_scope() as db:
        tid = await db.scalar(select(Tenant.id).where(Tenant.slug.like('natural-eval-%'))
                              .order_by(Tenant.created_at.desc()).limit(1))
    if tid is None:
        raise SystemExit('Run verify_natural_conversation.py first to create synthetic fixtures')
    model = get_llm_client()
    async with session_scope(tid) as db:
        user = await db.scalar(select(User).where(User.tenant_id == tid))
        for query, today in [('Deniz', True), ('Deniz', False), ('', True), ('', False)]:
            result = await read_messages(db, user, query=query, today=today)
            evidence = [{'id': 'messages', **result}]
            question = 'bu veriler kimlre ait? sadece bir kişniin mi yoksaa herkesin mi gün içindei'
            started = perf_counter()
            answer = await respond(model, question, evidence=evidence,
                                   context={'result_scope': [result['scope']]})
            record('scope', {'scope': result['scope'], 'question': question,
                             'answer': asdict(answer), 'seconds': round(perf_counter()-started, 2)})
    if scope_only:
        await dispose_engine()
        return
    config = CompanyAgentConfig.model_validate_json(Path('config/arti_kasnak.production.json').read_text())
    from src.modules.agents.company_runtime import CompanyAgentRuntime
    runtime = CompanyAgentRuntime(config, model)
    business_history = []
    fact_ids = ()
    for question in ['Captromal kasnağın güncel fiyatı kaç?', 'Biraz ara verelim, gökkuşağı nasıl oluşur?',
                     'İşe dönelim: bunun yarın kesin teslimi mümkün mü?']:
        started = perf_counter()
        result = await runtime.reply(question, history=business_history, context_fact_ids=fact_ids)
        record('customer_final', {'question': question, 'reply': result.reply,
                                  'verified': result.answer_verified, 'origin': result.answer_origin,
                                  'fact_ids': result.fact_ids, 'reason': result.fallback_reason,
                                  'seconds': round(perf_counter()-started, 2)})
        business_history.extend([LLMMessage(role='user', content=question),
                                 LLMMessage(role='assistant', content=result.reply)])
        if result.fact_ids:
            fact_ids = result.fact_ids
    history = []
    state = None
    for index, question in enumerate([
        'Teklif hazırlamak istiyorum.',
        'Benim adım Deniz Kaya.',
        'Soyadımı yanlış yazdım, Deniz Kaan olacak.',
        'Bir dakika, gökkuşağı neden oluşur?',
        'İşe dönelim, yeni kasnak seçmek istiyorum.',
    ]):
        started = perf_counter()
        turn, state, _ = await preview_natural(config, state, question, model, history,
                                               start_requested=index == 0)
        record('intake', {'question': question, 'reply': turn.reply,
                          'verified': turn.answer_verified, 'origin': turn.answer_origin,
                          'reason': turn.fallback_reason, 'state': state,
                          'seconds': round(perf_counter()-started, 2)})
        history.extend([LLMMessage(role='user', content=question), LLMMessage(role='assistant', content=turn.reply)])
    await dispose_engine()


if __name__ == '__main__':
    asyncio.run(main())
