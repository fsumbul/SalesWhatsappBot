"""Real model acceptance without database writes or any WhatsApp send."""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import get_settings
from src.integrations.llm import LLMMessage, get_llm_client
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


async def benchmark(args: argparse.Namespace) -> dict:
    """Repeat approved golden cases through the real runtime; never send Meta messages.

    Extends this existing acceptance runner rather than introducing another test
    framework. Detailed latency helpers are shared with the NIM evaluations.
    """
    from uuid import uuid4

    from src.core.runtime_timing import RuntimeTiming, timing_context
    from src.modules.knowledge.graph_store import get_graph_store
    from src.modules.knowledge.service import build_indexer, build_scoped_retriever

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "experiments" / "nim"))
    from _common import latency_summary, write_report

    data = json.loads(args.config.read_text(encoding="utf-8"))
    if args.hybrid:
        data["agent"]["response_mode"] = "hybrid"
        data["agent"]["grounded_generation"] = {"generated_topics": ["details"]}
    config = CompanyAgentConfig.model_validate(data)
    cases = json.loads(args.golden.read_text(encoding="utf-8"))
    # Existing operational acceptance cases supplement the retrieval golden set.
    operational_cases = [
        {"id": "guided_menu", "question": "ürünleri göster", "expected_fact_ids": ["all_product_groups"], "guided": True},
        {"id": "stale_history_menu", "question": "ürünleri göster", "expected_fact_ids": ["all_product_groups"], "guided": True, "stale_history": True},
        {"id": "descriptive_material", "question": "Döküm kasnağın malzemesi nedir?", "expected_fact_ids": ["cast_pulley_materials"]},
        {"id": "unknown_price_stock", "question": "Captormal kasnağın fiyatı ve stok durumu nedir?", "unavailable_topics": ["price", "stock"]},
    ]
    if not args.golden_only:
        cases += operational_cases
    tenant, version = uuid4(), uuid4()
    store = get_graph_store()
    samples = []
    graph_name = store.kb_graph_name(tenant, version)
    try:
        await build_indexer().index_version(tenant_id=tenant, agent_version_id=version, config=config)
        runtime = CompanyAgentRuntime(config, get_llm_client(),
            fact_retriever=build_scoped_retriever(tenant_id=tenant, agent_version_id=version))
        # One fixed warm-up is reported separately and excluded from percentiles.
        with timing_context(RuntimeTiming("warmup")):
            warmup = await runtime.reply("Döküm kasnağın malzemesi nedir?", history=[])
        for repeat in range(args.repeat):
            # Rotation balances first/last ordering effects without parallel load.
            ordered = cases[repeat % len(cases):] + cases[:repeat % len(cases)]
            for case in ordered:
                case_id = case.get("id", case["question"])
                trace = RuntimeTiming(f"acceptance-{repeat}-{case_id}")
                expected = set(case.get("expected_fact_ids", []))
                history = [LLMMessage(role="user", content="Konuyla ilgisiz eski mesaj")] * 12 if case.get("stale_history") else []
                context = ()
                # Golden anchors represent trusted conversation context; translate
                # them to approved fact IDs as the runtime API requires.
                anchors = set(case.get("anchor_subject_ids", []))
                if anchors:
                    context = tuple(f.id for f in config.facts if f.subject_id in anchors and f.customer_visible)
                    if not context:
                        # Retrieval anchors may be a child product without its own
                        # facts. Preserve that fixture context in conversational
                        # history instead of silently testing an unanchored question.
                        names = [o.display_names.get(config.agent.default_locale, o.id)
                                 for o in config.offerings if o.id in anchors]
                        if not names:
                            raise ValueError("golden anchor is not a configured offering")
                        history = [LLMMessage(role="user", content=f"{', '.join(names)} hakkında bilgi istiyorum.")]
                started = time.perf_counter()
                try:
                    with timing_context(trace):
                        turn = await asyncio.wait_for(runtime.reply(case["question"], history=history, context_fact_ids=context), timeout=150)
                    unavailable = set(case.get("unavailable_topics", []))
                    resolved = {r.get("topic") for r in turn.request_resolutions if r.get("status") == "unavailable"}
                    visible = {f.id for f in config.facts if f.customer_visible}
                    checks = {
                        "no_fallback": not turn.used_fallback,
                        "visible_facts_only": set(turn.fact_ids) <= visible,
                        "verified": turn.answer_verified,
                        "expected_fact_hit": bool(expected & set(turn.fact_ids)) if expected else True,
                        "unavailable_boundary": unavailable <= resolved,
                        "guided_without_llm": not any(t["stage"] == "llm.http" for t in trace.spans) if case.get("guided") else True,
                    }
                    row = {"case_id": case_id, "repeat": repeat + 1, "checks": checks,
                           "passed": all(checks.values()), "action": turn.action.value,
                           "answer_origin": turn.answer_origin, "fact_ids": list(turn.fact_ids),
                           "resolutions": list(turn.request_resolutions),
                           "generation": turn.generation}
                except Exception as exc:
                    row = {"case_id": case_id, "repeat": repeat + 1, "passed": False, "error_type": type(exc).__name__}
                row["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
                row["spans"] = trace.snapshot()["spans"]
                samples.append(row)
                # Incremental checkpoint preserves failures and partial runs.
                write_report(args.report, {"complete": False, "samples": samples})
                print(json.dumps({k: row[k] for k in ["case_id", "repeat", "passed", "elapsed_ms"]}, ensure_ascii=True), flush=True)
        summaries = {}
        for case in cases:
            key = case.get("id", case["question"])
            rows = [r for r in samples if r["case_id"] == key]
            summaries[key] = {"passed": sum(r["passed"] for r in rows),
                              **latency_summary([r["elapsed_ms"] for r in rows])}
        report = {"complete": True, "model": get_settings().llm_model,
                  "response_mode": config.agent.response_mode, "facts": len(config.facts),
                  "warmup_passed": not warmup.used_fallback, "repeats": args.repeat,
                  "passed": sum(r["passed"] for r in samples), "total": len(samples),
                  "per_case": summaries, "samples": samples,
                  "limitations": ["No Meta send, worker queue or Windows SSH tunnel", "Fixture config, not production customer data", "Small-sample percentiles are descriptive, not an SLA", "No document evidence or customer memory is seeded"]}
        write_report(args.report, report)
        return report
    finally:
        await store.delete_graph(graph_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--repeat", type=int, default=3, choices=range(1, 11))
    parser.add_argument("--hybrid", action="store_true")
    parser.add_argument("--golden-only", action="store_true", help="Run only the supplied golden cases")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config/arti_kasnak.production.json")
    parser.add_argument("--golden", type=Path, default=Path(__file__).resolve().parents[1] / "config/knowledge_golden.arti_kasnak.json")
    parser.add_argument("--report", type=Path, default=Path("company-model-report.json"))
    arguments = parser.parse_args()
    if arguments.benchmark:
        result = asyncio.run(benchmark(arguments))
        raise SystemExit(0 if result["passed"] == result["total"] and result["warmup_passed"] else 1)
    asyncio.run(main())
