"""Browser + local API + actual configured Qwen; no guided action or response fixture."""
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from playwright.async_api import async_playwright

BASE = os.environ.get("E2E_WEB_URL", "http://localhost:53010")


async def main():
    account = json.loads(Path(os.environ.get("E2E_ACCOUNT_FILE", "/tmp/sales-workflow-e2e-account.json")).read_text())
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        page.set_default_timeout(60000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(BASE + "/tr")
        await page.get_by_label("Şirket kodu").fill(account["slug"])
        await page.get_by_label("E-posta", exact=True).fill(account["email"])
        await page.get_by_label("Şifre", exact=True).fill(account["password"])
        await page.get_by_role("button", name="Giriş yap", exact=True).click()

        async def say(text):
            await page.get_by_label("Operasyon mesajı").fill(text)
            async with page.expect_response(lambda response: response.url.endswith("/turns") and response.request.method == "POST") as completed:
                await page.get_by_role("button", name="Gönder", exact=True).click()
            response = await completed.value
            assert response.status == 200
            data = await response.json()
            assert data["response_source"] == "model", data["response_source"]
            return data

        email = "model-" + uuid4().hex[:8] + "@example.com"
        await say(f"Deniz adlı müşteriyi {email} e-postasıyla ekle")
        card = page.get_by_role("region", name="Müşteri / irtibat ekle", exact=True)
        await card.get_by_label("Ad soyad").wait_for()
        assert await card.get_by_label("Ad soyad").input_value() == "Deniz"
        assert await card.get_by_label("E-posta", exact=False).input_value() == email
        await say("Adını Deniz Yılmaz olarak düzelt")
        await page.wait_for_function("() => Array.from(document.querySelectorAll('input')).some(input => input.value === 'Deniz Yılmaz')")
        assert await card.get_by_label("E-posta", exact=False).input_value() == email
        await page.reload()
        await page.get_by_role("button", name="Sohbet geçmişi", exact=True).click()
        await page.get_by_role("dialog").get_by_role("button", name="Deniz adlı müşteriyi", exact=False).first.click()
        assert await card.get_by_label("Ad soyad").input_value() == "Deniz Yılmaz"
        assert await page.get_by_label("Operasyon mesajı").count() == 1
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await card.scroll_into_view_if_needed()
        await page.screenshot(path="/tmp/sales-workflow-real-qwen-mobile.png", full_page=True)
        assert not errors, errors
        print(json.dumps({"checks": ["natural Qwen contact intent", "literal name and email", "Qwen updates same workflow", "email preserved", "reload persistence", "one composer", "mobile no overflow", "no JavaScript errors"], "model": "actual configured provider", "Meta": "not called"}))
        await browser.close()


asyncio.run(main())
