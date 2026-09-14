"""Actual Qwen + browser routing; stays before template reads or message queueing."""
import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright

BASE = os.environ.get("E2E_WEB_URL", "http://localhost:53010")

async def main():
    account = json.loads(Path(os.environ.get("E2E_ACCOUNT_FILE", "/tmp/sales-workflow-e2e-account.json")).read_text())
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        page.set_default_timeout(120000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(BASE + "/tr")
        await page.get_by_label("Şirket kodu").fill(account["slug"])
        await page.get_by_label("E-posta", exact=True).fill(account["email"])
        await page.get_by_label("Şifre", exact=True).fill(account["password"])
        await page.get_by_role("button", name="Giriş yap", exact=True).click()
        async def say(text):
            await page.get_by_label("Operasyon mesajı").fill(text)
            async with page.expect_response(lambda r: r.url.endswith("/turns") and r.request.method == "POST") as completed:
                await page.get_by_role("button", name="Gönder", exact=True).click()
            response = await completed.value
            assert response.status == 200, await response.text()
            data = await response.json()
            assert data["response_source"] == "model", data
            assert data["cards"] == []
            return data
        data = await say("+15550102030 ve +15550102031 numaralarına tanıtım gönder")
        row = data["workflows"][-1]
        assert row["kind"] == "outreach" and row["step"] == "details"
        card = page.get_by_role("region", name="WhatsApp gönderimi hazırla", exact=True).last
        assert set((await card.get_by_label("Alıcı telefonları").input_value()).split()) == {"+15550102030", "+15550102031"}
        status = await say("Gönderimin son durumu nedir?")
        assert status["workflows"][-1]["revision"] == row["revision"]
        cancelled = await say("Gönderimi iptal et")
        assert cancelled["workflows"][-1]["status"] == "cancelled"
        assert await page.get_by_label("Operasyon mesajı").count() == 1
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await card.scroll_into_view_if_needed()
        await page.screenshot(path="/tmp/sales-workflow-natural-outreach-mobile.png", full_page=True)
        assert not errors, errors
        print(json.dumps({"checks": ["actual Qwen starts shared outreach", "literal recipient list", "no legacy cards", "status preserves revision", "natural cancellation", "one composer", "mobile no overflow", "no JS errors"], "Meta": "not called"}))
        await browser.close()

asyncio.run(main())
