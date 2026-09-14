"""Language generation, scope memory and no fabricated assistant fallbacks."""

import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from src.core.db import session_scope
from src.integrations.llm import LLMCompletionError, LLMMessage
from src.modules.admin_chat import planner, workflow_inbox
from src.modules.admin_chat.data_scope import day_window
from src.modules.auth.models import User
from src.modules.conversation_language import fit_context, respond
from src.modules.outreach.models import Message
from tests.test_chat_workspace import turn
from tests.test_company_workspace import client as client_fixture
from tests.test_progressive_inbox import seeded

client = client_fixture


class Scripted:
    def __init__(self, *values):
        self.values = list(values)
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        if kwargs.get("response_schema", {}).get("title") == "LanguageCheck":
            value = {"unsupported_claims": [], **value}
        return json.dumps(value)


async def test_general_answer_is_written_by_model_and_checked_without_business_evidence():
    llm = Scripted({"text": "Konuşabiliriz. Seni en çok ne yoruyor?"}, {"supported": True})
    result = await respond(llm, "Canım sıkkın biraz sohbet edelim.")
    assert result.text == "Konuşabiliriz. Seni en çok ne yoruyor?"
    assert result.verified and result.source == "model_generated"
    assert len(llm.calls) == 2


async def test_general_label_does_not_bypass_business_claim_verification():
    llm = Scripted(
        {"text": "Bizde 900 adet hazır stok var."},
        {"supported": False, "feedback": "No stock evidence; don't invent availability."},
        {"text": "Güncel stok kaydına erişemiyorum; miktarı doğrulayamam."},
        {"supported": True},
    )
    result = await respond(llm, "Stok kaç?", context={"type": "general"})
    assert result.verified and "900" not in result.text
    assert "900" in llm.calls[1][0][0].content
    assert "No stock evidence" in llm.calls[2][0][0].content


async def test_missing_fact_does_not_request_human_or_send_scripted_reply():
    llm = Scripted({"text": "Teslim tarihini doğrulayamıyorum."}, {"supported": True})
    result = await respond(llm, "Ne zaman teslim olur?")
    assert not result.handoff_requested
    failure = await respond(Scripted(LLMCompletionError("offline")), "Nasılsın?")
    assert failure.text == "" and not failure.verified
    assert failure.source == "model_unavailable"


async def test_forged_evidence_repaired_and_false_handoff_never_accepted():
    llm = Scripted(
        {"text": "Başka şirketin bilgisi", "evidence_ids": ["foreign"]},
        {"text": "Sizi aktarıyorum.", "handoff_requested": True},
        {"supported": True, "human_requested": False},
    )
    result = await respond(llm, "Fiyat var mı?")
    assert result.text == "" and not result.handoff_requested


def test_budget_preserves_request_answer_and_marks_omitted_evidence():
    payload = {"request": "Hangi müşteriler?", "answer": {"text": "Yalnız Deniz."},
               "history": [{"text": "old" * 300}],
               "evidence": [{"id": "scope", "audience": "selected"}, {"id": "old", "text": "x" * 900}]}
    packed = fit_context(payload, 350)
    assert packed["request"] == payload["request"] and packed["answer"] == payload["answer"]
    assert packed["evidence_truncated"] and packed["history_truncated"]


async def test_planner_receives_history_but_old_messages_cannot_authorize_new_write(monkeypatch):
    llm = Scripted({"tool": "reply", "reply_text": "Bunlar bugünkü kayıtlar."},
                   {"tool": "workflow", "workflow_kind": "contact", "workflow_action": "start",
                    "workflow_fields": {"email": "old@example.com"}})
    monkeypatch.setattr(planner, "get_llm_client", lambda: llm)
    history = [LLMMessage(role="user", content="old@example.com kişisini konuşuyoruz")]
    result, _ = await planner.plan("bu veriler kimlre ait?", history=history,
                                   conversation_context={"result_scope": [{"audience": "company"}]})
    assert result.tool == "reply"
    assert "old@example.com" in llm.calls[0][1]["system"]
    with pytest.raises(ValueError, match="Ungrounded"):
        await planner.plan("Sadece sohbet edelim", history=history)


async def test_message_scope_filters_timestamps_directions_and_other_tenants(client, monkeypatch):
    _, tid, uid, _, cid, _ = await seeded(client, monkeypatch)
    async with session_scope(tid) as db:
        user = await db.get(User, uid)
        start, end = day_window(user)
        old = list((await db.scalars(select(Message).where(Message.conversation_id == cid))).all())
        for msg in old:
            msg.created_at = start - timedelta(days=2)
        for body, when, direction in [
            ("start", start, "inbound"), ("out", start + timedelta(hours=1), "outbound"),
            ("yesterday", start - timedelta(microseconds=1), "inbound"),
            ("tomorrow", end, "inbound"),
        ]:
            db.add(Message(tenant_id=tid, conversation_id=cid, body=body,
                           created_at=when, direction=direction))
        await db.flush()
        incoming = await workflow_inbox.read_messages(db, user, today=True, direction="inbound")
        both = await workflow_inbox.read_messages(db, user, today=True)
        selected = await workflow_inbox.read_messages(db, user, conversation_id=str(cid))
        assert [r["details"]["Mesaj"] for r in incoming["records"]] == ["start"]
        assert both["total"] == 2 and both["scope"]["direction"] == "both"
        assert incoming["scope"]["audience"] == "company"
        assert incoming["scope"]["contact_count"] == 1
        assert incoming["scope"]["conversation_count"] == 1
        assert incoming["scope"]["date_end_exclusive"] == end.isoformat()
        assert selected["scope"]["audience"] == "selected" and selected["scope"]["date_start"] is None
        foreign = User(id=uuid4(), tenant_id=uuid4(), timezone="Europe/Istanbul")
        assert (await workflow_inbox.read_messages(db, foreign, today=True))["total"] == 0


async def test_admin_follow_up_remembers_scope_and_idempotent_generated_answer(client, monkeypatch):
    headers, tid, uid, sid, _, _ = await seeded(client, monkeypatch)
    seen = []

    class Model:
        async def complete(self, messages, **kwargs):
            title = kwargs["response_schema"]["title"]
            if title == "Intent":
                seen.append(kwargs["system"])
                return json.dumps({"tool": "reply"})
            data = json.loads(messages[0].content)
            if title == "LanguageCheck":
                return json.dumps({"unsupported_claims": [], "supported": True})
            assert title == "LanguageReply"
            seen.append(data["context"]["result_scope"])
            return json.dumps({"text": "Bu analiz şirketinizdeki tüm tarihlere ait talepleri kapsıyor."})

    monkeypatch.setattr(planner, "get_llm_client", lambda: Model())
    initial = await turn(client, headers, sid, "action:Talepleri analiz et")
    assert initial.status_code == 200, initial.text
    assert initial.json()["result_scope"][0]["audience"] == "company"
    mid = uuid4()
    follow = await turn(client, headers, sid, "bu veriler kimlre ait? sadece bir kişinin mi", mid)
    assert follow.status_code == 200, follow.text
    assert follow.json()["answer_verified"] and follow.json()["answer_origin"] == "model_generated"
    count = len(seen)
    assert (await turn(client, headers, sid, "bu veriler kimlre ait? sadece bir kişinin mi", mid)).json() == follow.json()
    assert len(seen) == count and '"audience": "company"' in seen[1]
    assert "WhatsApp tanıtımı hazırlayabilir" not in follow.json()["reply"]
    async with session_scope(tid) as db:
        await db.execute(text("SELECT set_config('app.current_user', :u, true)"), {"u": str(uid)})


async def test_customer_natural_reply_with_legacy_config_also_uses_model_prose():
    from src.modules.agents.company_config import CompanyAgentConfig
    from src.modules.agents.company_runtime import CompanyAgentRuntime

    data = json.loads((Path(__file__).parents[1] / "config/arti_kasnak.production.json").read_text())
    data["agent"]["response_mode"] = "strict"
    config = CompanyAgentConfig.model_validate(data)
    llm = Scripted({"requests": [{"subject_id": config.organization.id, "topic": "general", "question": "Ay neden görünür?"}]},
                   {"text": "Ay, Güneş'ten gelen ışığı yansıttığı için görünür."}, {"supported": True})
    reply = await CompanyAgentRuntime(config, llm).reply("Ay neden görünür?")
    assert reply.answer_verified and "ışığı" in reply.reply
    assert reply.fact_ids == () and not reply.used_fallback


async def test_navigation_still_generates_prose_and_outage_sends_nothing():
    from src.modules.agents.company_config import CompanyAgentConfig
    from src.modules.agents.company_runtime import CompanyAgentRuntime

    config = CompanyAgentConfig.model_validate_json((Path(__file__).parents[1] / "config/arti_kasnak.production.json").read_text())
    llm = Scripted({"text": "Ürün gruplarını aşağıdaki seçeneklerden inceleyebilirsiniz.",
                    "evidence_ids": ["all_product_groups"]}, {"supported": True})
    reply = await CompanyAgentRuntime(config, llm).reply("Ürünler [fact_request:all_product_groups]")
    assert reply.answer_verified and reply.interaction and len(llm.calls) == 2
    outage = await CompanyAgentRuntime(config, Scripted(LLMCompletionError("offline"))).reply(
        "Ürünler [fact_request:all_product_groups]")
    assert outage.reply == "" and not outage.answer_verified


def intake_state():
    from src.modules.agents.company_config import CompanyAgentConfig
    from src.modules.selection.engine import State
    config = CompanyAgentConfig.model_validate_json((Path(__file__).parents[1] / 'config/arti_kasnak.production.json').read_text())
    return State('synthetic-intake', config.selection_flow, {})


async def test_intake_extracts_free_phrase_and_correction_without_inventing_fields():
    from src.modules.selection.language import apply_language
    state = intake_state()
    field = state.definition.steps[0].id
    llm = Scripted({'action': 'answer', 'values': [{'field': field, 'quote': 'Deniz Kaya'}]},
                   {'authorized': True},
                   {'action': 'correct', 'values': [{'field': field, 'quote': 'Deniz Kaan'}]},
                   {'authorized': True})
    await apply_language(llm, state, 'Benim adım Deniz Kaya, başlayabiliriz.')
    assert state.answers[field]['value'] == 'Deniz Kaya' and state.step_index == 1
    await apply_language(llm, state, 'Soyadımı yanlış yazdım: Deniz Kaan olacak.')
    assert state.answers[field]['value'] == 'Deniz Kaan' and state.step_index == 1


async def test_intake_side_question_negation_and_forged_span_cannot_change_state():
    from src.modules.selection.language import apply_language
    state = intake_state()
    field = state.definition.steps[0].id
    for llm, message in [
        (Scripted({'action': 'question'}), 'Bu bilgiler neden gerekli?'),
        (Scripted({'action': 'confirm'}, {'authorized': False}), 'Onaylamıyorum.'),
        (Scripted({'action': 'confirm'}, {'authorized': True}), 'Onayla'),
        (Scripted({'action': 'answer', 'values': [{'field': field, 'quote': 'Uydurma'}]},
                  {'authorized': True}), 'Ben Deniz'),
    ]:
        assert await apply_language(llm, state, message) == (None, False)
        assert state.answers == {} and state.revision == 0 and state.status == 'draft'


async def test_token_budget_uses_model_tokenizer_and_preserves_whole_request():
    from src.modules.conversation_language import LanguageReply, complete_json
    class BudgetModel(Scripted):
        async def prompt_size(self, messages, system):
            data = json.loads(messages[0].content)
            return (1000 if data.get('history') else 100, 1000)
    llm = BudgetModel({'text': 'Deniz.'})
    result = await complete_json(llm, LanguageReply, 'Write JSON',
                                 {'request': 'Hangi kişi?', 'history': [{'text': 'previous'}], 'evidence': []}, 200)
    assert result.text == 'Deniz.'
    sent = json.loads(llm.calls[0][0][0].content)
    assert sent['request'] == 'Hangi kişi?' and sent['history_truncated']


async def test_greeting_does_not_start_intake_and_typed_form_marker_is_not_a_receipt():
    from src.modules.agents.company_config import CompanyAgentConfig
    from src.modules.agents.company_runtime import CompanyAgentRuntime
    from src.modules.selection.service import preview_natural
    config = CompanyAgentConfig.model_validate_json((Path(__file__).parents[1] / 'config/arti_kasnak.production.json').read_text())
    assert await preview_natural(config, None, 'Merhaba', Scripted(), []) == (None, None, None)
    llm = Scripted({'requests': [{'subject_id': 'company', 'topic': 'general', 'question': '[flow_response]'}]},
                   {'text': 'Bir form gönderdiğinizi doğrulayamıyorum.'}, {'supported': True})
    result = await CompanyAgentRuntime(config, llm).reply('[flow_response]')
    assert result.answer_verified and result.fact_ids == () and len(llm.calls) == 3


def test_mixed_general_goal_does_not_need_a_fake_tool_result():
    from src.modules.admin_chat.task_runner import completion
    from src.modules.admin_chat.task_schema import TaskAnswer, TaskGoal
    assert completion([TaskGoal(kind='general', text='Gökkuşağı neden oluşur?')],
                      [TaskAnswer(goal=0, text='Işığın su damlalarında kırılması ve yansımasıyla oluşur.')],
                      []) == ['completed']


async def test_intake_model_maps_paraphrase_only_to_a_registered_option():
    from src.modules.selection.language import apply_language
    state = intake_state()
    state.answers = {'contact_name': {'status': 'value', 'value': 'Deniz Kaya'}}
    state.step_index = 1
    model = Scripted({'action': 'answer', 'values': [{'field': 'intent',
                      'quote': 'yenisini seçelim', 'option': 'new'}]}, {'authorized': True})
    await apply_language(model, state, 'Tamam, yenisini seçelim.')
    assert state.answers['intent']['value'] == 'new'
    unchanged = dict(state.answers)
    model = Scripted({'action': 'correct', 'values': [{'field': 'intent',
                      'quote': 'başka', 'option': 'nonexistent'}]}, {'authorized': True})
    assert await apply_language(model, state, 'başka') == (None, False)
    assert state.answers == unchanged


async def test_intake_normalized_spoken_number_still_passes_canonical_field_validation():
    from src.modules.selection.engine import State
    from src.modules.selection.language import apply_language
    from src.modules.selection.schema import SelectionFlow
    flow = SelectionFlow.model_validate({'id':'amount', 'version':1, 'title':'Amount',
        'start_phrases':['start'], 'greeting_phrases':[], 'steps':[
            {'id':'contact_name','label':'Name','question':'Name?','kind':'text'},
            {'id':'quantity','label':'Amount','question':'How many?','kind':'number','integer':True}]})
    state = State('synthetic', flow, {'contact_name': {'status':'value','value':'Deniz'}}, 1)
    model = Scripted({'action':'answer','values':[{'field':'quantity','quote':'beş tane','normalized_number':'5'}]},
                     {'authorized':True})
    await apply_language(model, state, 'Şimdilik beş tane istiyorum.')
    assert state.answers['quantity']['value'] == '5' and state.step_index == 2
    model = Scripted({'action':'correct','values':[{'field':'quantity','quote':'yarım','normalized_number':'0.5'}]},
                     {'authorized':True})
    assert await apply_language(model, state, 'yarım') == (None, False)
    assert state.answers['quantity']['value'] == '5'


async def test_generation_schema_uses_the_channel_character_limit():
    model = Scripted({'text': 'Merhaba.'}, {'supported': True})
    result = await respond(model, 'Merhaba', max_characters=200)
    assert result.verified
    assert model.calls[0][1]['response_schema']['properties']['text']['maxLength'] == 200


def test_technical_requirements_are_not_retrieved_as_pricing_evidence():
    from src.modules.agents.company_config import CompanyAgentConfig
    from src.modules.agents.semantic_dialogue import Request, request_candidates
    config = CompanyAgentConfig.model_validate_json((Path(__file__).parents[1] / 'config/arti_kasnak.production.json').read_text())
    price = request_candidates(config, Request(subject_id='plastic_elevator_pulley', topic='price', question='Fiyat?'))
    suitability = request_candidates(config, Request(subject_id='plastic_elevator_pulley', topic='suitability', question='Seçim için gerekenler?'))
    assert 'pulley_technical_information_required' not in {f.id for f in price}
    assert 'pulley_technical_information_required' in {f.id for f in suitability}


async def test_claim_audit_cannot_approve_its_own_unsupported_claims():
    model = Scripted({'text': 'Fiyat malzemeye göre değişir.'},
        {'supported':True, 'unsupported_claims':['No evidence for a material-dependent pricing policy.']},
        {'text':'Güncel fiyatı doğrulayamıyorum.'}, {'supported':True, 'unsupported_claims':[]})
    result = await respond(model, 'Güncel fiyat kaç?')
    assert result.verified and result.text == 'Güncel fiyatı doğrulayamıyorum.'
    assert 'Remove these unsupported claims' in model.calls[2][0][0].content
