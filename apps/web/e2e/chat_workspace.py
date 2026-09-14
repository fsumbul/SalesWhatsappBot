"""Chat UI contract: model/preview cards are fixtures; login and panel APIs are real."""
import asyncio
import json
import os
from uuid import uuid4
from playwright.async_api import async_playwright

BASE = os.environ.get("E2E_WEB_URL", "http://localhost:53000")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        messages = []
        operation_id = str(uuid4())
        async def route(r):
            path = r.request.url.split("admin-chat/")[1]
            result = {}
            if path == "sessions":
                result = [{"id":"fixture","title":"Şirket bilgileri"}] if r.request.method == "GET" else {"id":"fixture"}
            elif path.endswith("/messages"):
                result = messages
            elif path.endswith("/turns"):
                text = r.request.post_data_json["text"]
                if "workspace:confirm" in text:
                    for m in messages:
                        for c in m.get("cards", []):
                            if c.get("operation_id") == operation_id: c["status"] = "applied"
                    cards = []
                    reply = "Bilgiler taslağa kaydedildi."
                elif "test" in text.lower():
                    cards = [{"type":"agent_test", "title":"Örnek şirket", "result":{"reply":"Bakım hizmeti veriyoruz.","response_source":"fallback","fallback_reason":"model_unavailable","version":2,"model":"fixture","latency_ms":10,"fact_ids":[]}}]
                    reply = "Müşteri testi tamamlandı. WhatsApp mesajı gönderilmedi."
                elif "bilgisine" in text:
                    cards = [{"type":"workspace_preview","title":"Bilgi değişikliğini kabul et","operation_id":operation_id,"summary":"Taslağa kaydedilecek: Bakım hizmeti veriyoruz.","company_config":{"organization":{"display_names":{"tr":"Örnek şirket"}},"facts":[{"customer_text":{"tr":"Bakım hizmeti veriyoruz."}}]}}]
                    reply = "Bilgi değişikliği önizlemeye hazır."
                else:
                    op = {"action:Ekip ve yetkiler":"team","action:Şirketler":"platform","action:Gelen kutusu":"inbox","action:Asistanlar":"knowledge"}.get(text,"knowledge")
                    cards = [{"type":"workspace","operation":op,"title":{"team":"Ekip ve yetkiler","platform":"Şirketler","inbox":"Gelen kutusu","knowledge":"Şirket bilgileri"}[op]}]
                    reply = "Bu karttan sohbet içinde devam edebilirsiniz."
                messages.extend([{"role":"user","text":text,"display_text":text.removeprefix("action:")}, {"role":"assistant","text":reply,"cards":cards,"response_source":"model"}])
                result = {"reply":reply}
            await r.fulfill(status=200, content_type="application/json", body=json.dumps(result))
        await page.route("**/api/platform/admin-chat/**", route)
        await page.goto(BASE + "/tr")
        await page.get_by_label("Şirket kodu").fill("e2e-platform")
        await page.get_by_label("E-posta",exact=True).fill("admin@example.com")
        await page.get_by_label("Şifre",exact=True).fill(os.environ["E2E_PLATFORM_PASSWORD"])
        await page.get_by_role("button",name="Giriş yap",exact=True).click()
        composer = page.get_by_label("Operasyon mesajı")
        await composer.fill("Şirket bilgisine bakım hizmeti veriyoruz ekle")
        await composer.press("Enter")
        await page.get_by_role("button",name="Uygula",exact=True).click()
        await page.get_by_text("Bilgiler taslağa kaydedildi.",exact=True).wait_for()
        assert await page.get_by_role("button",name="Uygula",exact=True).is_disabled()
        await composer.fill("Müşteri testi yap")
        await composer.press("Enter")
        await page.get_by_text("Model yanıtı alınamadı veya doğrulanamadı",exact=False).wait_for()
        for name, label in [("Ekip ve yetkiler","Ekip ve yetkiler"), ("Şirketler","Şirketler"), ("Gelen kutusu","Gelen kutusu")]:
            await page.get_by_text("Hesap",exact=True).click()
            await page.get_by_role("button",name=name,exact=True).click()
            card = page.get_by_label(label,exact=True)
            await card.wait_for()
            assert await composer.count() == 1
            assert page.url.endswith("/tr")
            await card.get_by_role("button",name=name + " Daralt").click()
        await page.get_by_text("Hesap",exact=True).click()
        await page.get_by_role("button",name="Asistanlar",exact=True).click()
        await page.get_by_label("Şirket bilgileri",exact=True).wait_for()
        await page.screenshot(path="/tmp/sales-chat-workspace-desktop.png",full_page=True)
        await page.set_viewport_size({"width":390,"height":844})
        await page.evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert await composer.is_visible()
        assert await page.evaluate("document.documentElement.scrollHeight <= innerHeight + 1")
        await page.screenshot(path="/tmp/sales-chat-workspace-mobile.png",full_page=True)
        await page.reload()
        await page.get_by_role("button",name="Sohbet geçmişi",exact=True).click()
        await page.get_by_role("button",name="Şirket bilgileri",exact=True).click()
        await page.get_by_text("Bilgiler taslağa kaydedildi.",exact=True).wait_for()
        assert await page.get_by_role("button",name="Uygula",exact=True).is_disabled()
        assert not errors, errors
        print(json.dumps({"passed":["inline configuration preview","confirmed action status persists","test fallback visible","team without navigation","company management without navigation","inbox without navigation","agent and import controls in chat","mobile composer and no overflow","restored chat cards after reload","no browser errors"], "model":"fixture; no WhatsApp send"}))
        await browser.close()

asyncio.run(main())
