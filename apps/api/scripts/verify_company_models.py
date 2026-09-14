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
        records.append(turn.response_source=="model" and not turn.used_fallback)
    from src.modules.admin_chat.planner import plan
    for message, expected in [
        ("Meta mesaj limitimiz ne kadar?", "capacity"),
        ("Teklifleri görüntüle", "quotes"),
        ("+15550102030 ve +15550102031 numaralarına tanıtım gönder", "outreach"),
        ("Hazırladığın tanıtımı gönder", "send_outreach"),
        ("Gönderimin son durumu nedir?", "outreach_status"),
        ("Şirket bilgilerimizi göster", "workspace:knowledge"),
        ("Şirket bilgisine bakım hizmeti sunduğumuzu ekle", "workspace:configure"),
        ("Müşteri testinde Hangi hizmetleri sunuyorsunuz? mesajını dene", "workspace:test"),
        ("Taslağı yayınla", "workspace:publish"),
        ("Ekibimizin yetkilerini göster", "workspace:team"),
        ("demo@example.com adresini izleyici olarak davet et", "workspace:invite"),
        ("Şirketleri listele", "workspace:platform"),
        ("Gelen kutusunu aç", "workspace:inbox"),
    ]:
        try:
            intent, source = await plan(message)
            expected_tool, _, expected_operation = expected.partition(":")
            passed = intent.tool == expected_tool and source == "model"
            if expected_operation:
                passed = passed and intent.operation == expected_operation
            if expected == "outreach":
                passed = passed and set(intent.recipients) == {"+15550102030", "+15550102031"}
            print(json.dumps({"operation": message, "tool": intent.tool, "workspace_operation": intent.operation, "source": source, "passed": passed}, ensure_ascii=True), flush=True)
            records.append(passed)
        except Exception as exc:
            print(json.dumps({"operation": message, "error": type(exc).__name__, "passed": False}, ensure_ascii=True), flush=True)
            records.append(False)
    print(json.dumps({"model":get_settings().llm_model,"passed":all(records),"checks":len(records)}))
    if not all(records):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
