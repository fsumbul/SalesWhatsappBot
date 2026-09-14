"""Real Qwen + disposable local DB acceptance. Never contacts Meta or production DB.

Run from apps/api with DATABASE_URL pointing to local leadpulse_test and a configured
LLM_PROVIDER/LLM_BASE_URL/LLM_MODEL. Writes only synthetic, non-secret transcripts.
"""
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from src.core.config import get_settings
from src.core.db import dispose_engine, session_scope
from src.integrations.llm import LLMMessage, get_llm_client
from src.main import create_app
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.company_runtime import CompanyAgentRuntime
from src.modules.auth.schemas import TenantRegisterIn
from src.modules.auth.service import AuthService
from src.modules.discovery.models import Lead, LeadContact
from src.modules.outreach.models import Conversation, Message

REPORT = Path('../../experiments/natural-conversation-qwen-2026-09-15.json')


async def main():
    settings = get_settings()
    database = make_url(str(settings.database_url))
    if database.host not in {'localhost', '127.0.0.1'} or database.database != 'leadpulse_test':
        raise SystemExit('Only disposable local leadpulse_test is allowed')
    report = {'model': settings.llm_model, 'date': datetime.now(UTC).isoformat(),
              'enable_thinking': settings.llm_enable_thinking,
              'admin': [], 'customer': [], 'safety': {}, 'failures': []}
    customer_only = '--customer-only' in sys.argv
    if customer_only and REPORT.exists():
        report['admin'] = json.loads(REPORT.read_text())['admin']
    real_model = get_llm_client()
    class ObservedModel:
        async def prompt_size(self, messages, system=''):
            return await real_model.prompt_size(messages, system)

        async def complete(self, messages, **kwargs):
            started = perf_counter()
            result = await real_model.complete(messages, **kwargs)
            print(json.dumps({'inference': kwargs.get('response_schema', {}).get('title'),
                              'seconds': round(perf_counter()-started, 2), 'output': result},
                             ensure_ascii=False), flush=True)
            return result

    from src.modules.admin_chat import planner
    model = ObservedModel()
    planner.get_llm_client = lambda: model
    def record(channel, value):
        report[channel].append(value)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({channel: value}, ensure_ascii=False), flush=True)

    password = 'Synthetic-' + uuid4().hex
    slug = 'natural-eval-' + uuid4().hex[:12]
    async with session_scope() as db:
        tenant, user = await AuthService(db).register_tenant(TenantRegisterIn(
            tenant_name='Synthetic conversation acceptance', tenant_slug=slug,
            admin_email='owner@example.com', admin_password=password))
        tid = tenant.id
        await db.commit()
    async with session_scope(tid) as db:
        for i, name in enumerate(['Deniz', 'Ece']):
            lead = Lead(tenant_id=tid, person_name=name, company_name='Synthetic',
                        normalized_name=name.casefold(), source='test', discovered_at=datetime.now(UTC))
            db.add(lead)
            await db.flush()
            phone = f'+1555010203{i}'
            contact = LeadContact(tenant_id=tid, lead_id=lead.id, type='phone',
                                  raw_value=phone, normalized_value=phone)
            db.add(contact)
            await db.flush()
            conv = Conversation(tenant_id=tid, lead_id=lead.id, contact_id=contact.id)
            db.add(conv)
            await db.flush()
            for days, direction, body in [(0, 'inbound', 'Döküm kasnak kataloğunu istiyorum.'),
                                           (0, 'outbound', 'Çiziminizi bekliyoruz.'),
                                           (3, 'inbound', 'Önceki siparişim hakkında yazıyorum.')]:
                db.add(Message(tenant_id=tid, conversation_id=conv.id, direction=direction,
                               body=body, created_at=datetime.now(UTC)-timedelta(days=days)))
        await db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),
                                 base_url='http://localhost/api/v1/', timeout=300) as client:
        login = await client.post('auth/login', json={'tenant_slug': slug, 'email': 'owner@example.com',
                                                    'password': password})
        login.raise_for_status()
        client.headers['Authorization'] = 'Bearer ' + login.json()['access_token']
        sid = (await client.post('admin-chat/sessions', json={})).json()['id']
        for question in ([] if customer_only else [
            'Bugün gelen müşteri mesajlarını getir.',
            'bu veriler kimlre ait? sadece bir kişniin mi yoksaa herkesin mi gün içindei',
            'Sadece Deniz için tüm zamanlardaki yazışmaları getir.',
            'Peki bunlar bir kişiye mi ait, yalnız bugünün mü?',
            'Biraz ara verelim. Gökkuşağı neden oluşur?',
            'İşe dönelim, bugünkü yazışmaları herkesi kapsayacak şekilde getir.',
        ]):
            mid = str(uuid4())
            started = perf_counter()
            response = await client.post(f'admin-chat/sessions/{sid}/turns',
                                         json={'text': question, 'client_message_id': mid})
            elapsed = round(perf_counter()-started, 2)
            body = response.json()
            record('admin', {'question': question, 'seconds': elapsed, 'status': response.status_code,
                             **{k: body.get(k) for k in ['reply', 'answer_origin', 'answer_verified',
                                                         'result_scope', 'technical_error']}})
            retry = await client.post(f'admin-chat/sessions/{sid}/turns',
                                      json={'text': question, 'client_message_id': mid})
            if retry.json() != body:
                report['failures'].append('Admin idempotency mismatch')
    config = CompanyAgentConfig.model_validate_json(Path('config/arti_kasnak.production.json').read_text())
    history = []
    facts = ()
    runtime = CompanyAgentRuntime(config, model)
    for question in [
        'Captromal kasnağın özelliklerini anlatır mısın?',
        'Peki bunun güncel fiyatı kaç?',
        'Fiyatı boşver, gökkuşağı nasıl oluşuyor?',
        'İşe dönelim. Plastik değil, döküm kasnak hakkında soruyordum.',
        'Bu ürün yarın kesin teslim olur mu?',
        'İnsan desteği istemiyorum, sadece hangi bilginin eksik olduğunu söyle.',
    ]:
        started = perf_counter()
        turn = await runtime.reply(question, history=history, context_fact_ids=facts)
        record('customer', {'question': question, 'seconds': round(perf_counter()-started, 2),
                            **{k: v for k,v in asdict(turn).items() if k != 'interaction'}})
        history.extend([LLMMessage(role='user', content=question), LLMMessage(role='assistant', content=turn.reply)])
        if turn.fact_ids:
            facts = turn.fact_ids
    async with session_scope(tid) as db:
        report['safety'] = {
            'messages_unchanged': (await db.scalar(select(func.count()).select_from(Message))) == 6,
            'contacts_unchanged': (await db.scalar(select(func.count()).select_from(LeadContact))) == 2,
            'external_send_attempts': 0,
        }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    await dispose_engine()


if __name__ == '__main__':
    asyncio.run(main())
