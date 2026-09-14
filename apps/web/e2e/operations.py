"""Chromium UI contract for inline operation cards; Meta/model are fixture responses.
Real auth is used. Real PostgreSQL/provider-boundary coverage lives in test_chat_outbound.py.
"""
import asyncio
import json
import os
from datetime import datetime, UTC
from playwright.async_api import async_playwright

BASE = os.environ.get("E2E_WEB_URL", "http://localhost:53000")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1080})
        errors=[]
        page.on("pageerror", lambda e: errors.append(str(e)))
        cap = {"type":"capacity", "connected":True, "meta_limit":2000, "meta_available":True, "local_cap":1000, "local_used":12,"local_remaining":988,"checked_at":datetime.now(UTC).isoformat(),"summary":"Meta limiti portföyde paylaşılır. Kalan Meta kapasitesi alınamıyor; yerel sayaç ayrı gösterilir."}
        batch = {"type":"outbound", "batch_id":"fixture-batch", "status":"draft", "template_name":"ilk_bilgilendirme", "summary":"Merhaba {{1}}, Artı Kasnak ürünleri hakkında bilgi almak ister misiniz?", "buttons":["Bilgi Al", "İlgilenmiyorum"], "variables":["1"], "values":{}, "consent_evidence":"", "recipients":[{"phone":"+15550102030","status":"draft","reason":"Tanıtım izni kaydı gerekli."},{"phone":"+15550102031","status":"draft","reason":"Tanıtım izni kaydı gerekli."}]}
        messages=[]
        requests=[]
        async def route(r):
            path=r.request.url.split("admin-chat/")[1]
            result={}
            if path=="capacity": result=cap
            elif path=="sessions": result=[] if r.request.method=="GET" else {"id":"fixture-session","title":"Tanıtım"}
            elif path.endswith("/turns"):
                value=r.request.post_data_json["text"]
                messages.extend([{"role":"user","text":value}, {"role":"assistant","text":"İki numara için tanıtım önizlemesi hazır." if "tanıtım" in value else "WhatsApp kapasitesi", "response_source":"model", "cards":[batch] if "tanıtım" in value else [cap]}])
                result={"reply":"Hazır"}
            elif path.endswith("/workflows"): result=[]
            elif path.endswith("/messages"): result=messages
            elif path.endswith("/actions"):
                payload=r.request.post_data_json
                requests.append(payload)
                batch["values"]=payload["variables"]
                batch["consent_evidence"]=payload["consent_evidence"]
                if payload["action"]=="send":
                    batch["status"]="queued"
                    for item in batch["recipients"]: item.update(status="queued",reason=None)
                result=batch
            elif path.startswith("batches/"): result=batch
            await r.fulfill(status=200,content_type="application/json",body=json.dumps(result))
        await page.route("**/api/platform/admin-chat/**",route)
        await page.goto(BASE+"/tr")
        await page.get_by_label("Şirket kodu").fill("e2e-platform")
        await page.get_by_label("E-posta",exact=True).fill("admin@example.com")
        await page.get_by_label("Şifre",exact=True).fill(os.environ["E2E_PLATFORM_PASSWORD"])
        await page.get_by_role("button",name="Giriş yap",exact=True).click()
        await page.get_by_role("heading",name="Bugün neye bakalım?",exact=True).wait_for()
        assert not await page.get_by_text("E2E Platform",exact=True).count()
        assert not await page.get_by_text("2.000",exact=True).count()
        await page.screenshot(path="/tmp/sales-simple-chat-desktop.png",full_page=True)
        await page.set_viewport_size({"width":390,"height":844})
        await page.evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        assert await page.evaluate("document.documentElement.scrollHeight <= innerHeight + 1")
        await page.screenshot(path="/tmp/sales-simple-chat-mobile.png",full_page=True)
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await page.set_viewport_size({"width":1440,"height":1080})
        await page.get_by_role("button", name="WhatsApp limiti", exact=True).click()
        await page.get_by_text("2.000",exact=True).wait_for()
        await page.get_by_label("Operasyon mesajı").fill("+15550102030\n+15550102031\nnumaralarına tanıtım hazırla")
        await page.get_by_role("button",name="Gönder",exact=True).click()
        await page.get_by_label("Şablon alanı 1").fill("Müşterimiz")
        await page.get_by_text("Eksik tanıtım iznini kaydet",exact=True).click()
        await page.get_by_label("İzin kaynağı ve tarihi").fill("14.09.2026 web formu izin kaydı TEST-UI")
        await page.screenshot(path="/tmp/sales-operations-desktop.png",full_page=True)
        await page.get_by_role("button",name="Taslağı kaydet",exact=True).click()
        await page.get_by_role("button",name="Uygun alıcılara gönder",exact=True).click()
        await page.get_by_role("button",name="Gönderimi iptal et",exact=True).wait_for()
        assert requests[-1]["action"]=="send"
        assert requests[-1]["variables"]=={"1":"Müşterimiz"}
        assert "TEST-UI" in requests[-1]["consent_evidence"]
        batch["status"]="completed"
        for item in batch["recipients"]: item.update(status="delivered")
        await page.get_by_text("Teslim edildi",exact=True).first.wait_for(timeout=15000)
        await page.set_viewport_size({"width":390,"height":844})
        await page.screenshot(path="/tmp/sales-operations-mobile.png",full_page=True)
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        print(json.dumps({"checks": ["simple chat home without test branding", "capacity only when requested", "Meta vs local capacity", "multiline recipient list", "inline template preview and quick replies", "consent and variables saved", "queue action in chat", "delivery status refresh", "mobile no overflow", "no browser errors"],"provider":"fixture; no Meta send"}))
        await browser.close()

asyncio.run(main())
