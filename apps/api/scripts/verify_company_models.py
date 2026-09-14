"""Real model acceptance without database writes or any WhatsApp send."""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import get_settings
from src.integrations.llm import get_llm_client
from src.modules.agents.company_config import CompanyAgentConfig
from src.modules.agents.company_runtime import CompanyAgentRuntime


async def main() -> None:
    llm = get_llm_client()
    records = []
    for name, fact in [("Örnek Metal", "Endüstriyel metal parça üretimi yapıyoruz."),
                       ("Örnek Eğitim", "Kurumlara yabancı dil eğitimi sunuyoruz.")]:
        config = CompanyAgentConfig.model_validate({
            "lifecycle":"approved", "organization":{"display_names":{"tr":name}},
            "agent":{"purposes":["information"],"supported_locales":["tr"],"default_locale":"tr"},
            "facts":[{"id":"services","subject_id":"company","category":"capability","value":fact,
                      "source":"synthetic acceptance fixture","customer_visible":True,"customer_text":{"tr":fact}}]})
        started=time.monotonic()
        turn=await CompanyAgentRuntime(config,llm).reply("Hangi hizmetleri sunuyorsunuz?")
        record={"company":name,"reply":turn.reply,"source":turn.response_source,"fallback_reason":turn.fallback_reason,"seconds":round(time.monotonic()-started,2)}
        print(json.dumps(record,ensure_ascii=True),flush=True)
        records.append(turn.response_source=="model" and not turn.used_fallback and turn.reply==fact)
    config=CompanyAgentConfig.model_validate_json((Path(__file__).resolve().parents[1]/"config/arti_kasnak.production.json").read_text(encoding="utf-8"))
    for message in ["Captromal kasnak özelliklerini anlatır mısınız?", "Captormal kasnağın fiyatı ve stok durumu nedir?"]:
        started=time.monotonic()
        turn=await CompanyAgentRuntime(config,llm).reply(message)
        print(json.dumps({"company":"Kasnak","input":message,"reply":turn.reply,"source":turn.response_source,"fallback_reason":turn.fallback_reason,"fact_ids":turn.fact_ids,"resolutions":turn.request_resolutions,"seconds":round(time.monotonic()-started,2)},ensure_ascii=True),flush=True)
        correct_boundary = (
            "plastic_pulley_performance" in turn.fact_ids
            if "özelliklerini" in message
            else all(any(r.get("topic") == topic and r.get("status") == "unavailable" and r.get("subject_id") == "plastic_elevator_pulley" for r in turn.request_resolutions) for topic in ("price", "stock"))
        )
        records.append(turn.response_source=="model" and not turn.used_fallback and correct_boundary)
    from src.modules.admin_chat.planner import plan
    for message, expected in [
        ("Meta mesaj limitimiz ne kadar?", "capacity"),
        ("Teklifleri görüntüle", "quotes"),
        ("+15550102030 ve +15550102031 numaralarına tanıtım gönder", "outreach"),
        ("Hazırladığın tanıtımı gönder", "send_outreach"),
        ("Gönderimin son durumu nedir?", "outreach_status"),
        ("Şirket bilgilerimizi göster", "workflow:records:knowledge"),
        ("Şirket bilgisine bakım hizmeti sunduğumuzu ekle", "workflow:configure"),
        ("Müşteri testinde Hangi hizmetleri sunuyorsunuz? mesajını dene", "workflow:test"),
        ("Taslağı yayınla", "workflow:publish"),
        ("Ekibimizin yetkilerini göster", "workflow:records:team"),
        ("demo@example.com adresini izleyici olarak davet et", "workflow:invite"),
        ("Şirketleri listele", "workflow:records:companies"),
        ("Gelen kutusunu aç", "workflow:records:inbox"),
    ]:
        try:
            intent, source = await plan(message)
            expected_tool, _, expected_operation = expected.partition(":")
            expected_operation, _, expected_category = expected_operation.partition(":")
            passed = intent.tool == expected_tool and source == "model"
            if expected_operation:
                passed = passed and (intent.workflow_kind if expected_tool == "workflow" else intent.operation) == expected_operation
            if expected_category:
                passed = passed and intent.workflow_fields.get("category") == expected_category
            if expected == "outreach":
                passed = passed and set(intent.recipients) == {"+15550102030", "+15550102031"}
            print(json.dumps({"operation": message, "tool": intent.tool, "workspace_operation": intent.operation, "workflow_kind": intent.workflow_kind, "workflow_fields": intent.workflow_fields, "source": source, "passed": passed}, ensure_ascii=True), flush=True)
            records.append(passed)
        except Exception as exc:
            print(json.dumps({"operation": message, "error": type(exc).__name__, "passed": False}, ensure_ascii=True), flush=True)
            records.append(False)
    # Natural-language progressive workflow checks; these never invoke write tools.
    workflow_cases = [
        ("Kişi eklemek istiyorum", [], "start", "person", {}),
        ("Deniz adlı müşteriyi deniz@example.com e-postasıyla ekle", [], "start", "contact", {"name": "Deniz", "email": "deniz@example.com"}),
        ("Adını Deniz Yılmaz olarak düzelt", [{"kind":"contact","step":"details","status":"awaiting_input","fields":{"name":"Deniz"}}], "update", None, {"name":"Deniz Yılmaz"}),
        ("Kişi eklemeye geri dön", [{"kind":"contact","step":"details","status":"paused","fields":{"name":"Deniz"}}], "resume", "contact", {}),
        ("Kişiyi kaydet", [{"kind":"contact","step":"review","status":"ready","fields":{"name":"Deniz","email":"deniz@example.com"}}], "complete", None, {}),
    ]
    workflow_cases.extend([
        ("Şirket sahibini owner@example.com adresinden yeniden davet et", [], "start", "owner_invite", {"email": "owner@example.com"}),
        ("Şirket sahibini davet etmek istiyorum", [], "start", "owner_invite", {}),
        ("Sahip davetini oluştur", [{"kind": "owner_invite", "step": "review", "status": "ready", "fields": {"email": "owner@example.com", "tenant": "synthetic-tenant"}}], "complete", None, {}),
    ])
    for message, context, action, kind, fields in workflow_cases:
        try:
            intent, source = await plan(message, workflow_context=context)
            passed = source == "model" and intent.tool == "workflow" and intent.workflow_action == action
            passed = passed and (kind is None or intent.workflow_kind == kind)
            passed = passed and all(intent.workflow_fields.get(k) == v for k,v in fields.items())
            if kind == "owner_invite":
                passed = passed and not intent.workflow_fields.get("tenant")
                passed = passed and not intent.workflow_fields.get("role")
                if not fields:
                    passed = passed and not intent.workflow_fields.get("email")
            records.append(passed)
            print(json.dumps({"workflow_input":message,"passed":passed,"action":intent.workflow_action,"kind":intent.workflow_kind},ensure_ascii=True),flush=True)
        except Exception as exc:
            records.append(False)
            print(json.dumps({"workflow_input":message,"passed":False,"error":type(exc).__name__},ensure_ascii=True),flush=True)
    print(json.dumps({"model":get_settings().llm_model,"passed":all(records),"checks":len(records)}))
    if not all(records):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
