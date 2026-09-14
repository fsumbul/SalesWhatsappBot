"""Local Chromium verification: preparation persists without a WhatsApp connection."""
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
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(BASE + "/tr")
        await page.get_by_label("Şirket kodu").fill(account["slug"])
        await page.get_by_label("E-posta", exact=True).fill(account["email"])
        await page.get_by_label("Şifre", exact=True).fill(account["password"])
        await page.get_by_role("button", name="Giriş yap", exact=True).click()
        await page.get_by_role("button", name="WhatsApp gönderimi hazırla", exact=True).click()
        card = page.get_by_role("region", name="WhatsApp gönderimi hazırla", exact=True)
        await card.get_by_label("Alıcı telefonları").fill("+15550102030")
        await card.get_by_label("Gönderimin amacı").fill("Yeni ürün bilgisi")
        await card.get_by_role("button", name="Devam et", exact=True).click()
        try:
            await card.get_by_text("İşlem başarısız", exact=True).wait_for()
        except Exception:
            print(await card.inner_text(), flush=True)
            raise
        assert await card.get_by_label("Alıcı telefonları").input_value() == "+15550102030"
        await page.reload()
        await page.get_by_role("button", name="Sohbet geçmişi", exact=True).click()
        await page.get_by_role("dialog").get_by_role("button", name="WhatsApp gönderimi hazırla", exact=False).first.click()
        card = page.get_by_role("region", name="WhatsApp gönderimi hazırla", exact=True)
        assert await card.get_by_label("Gönderimin amacı").input_value() == "Yeni ürün bilgisi"
        await card.get_by_role("button", name="Beklet", exact=True).click()
        await card.get_by_role("button", name="Kaldığım yerden devam et", exact=True).click()
        await card.get_by_text("Bilgi bekliyor", exact=True).wait_for()
        await card.locator("form input, form textarea").first.wait_for(state="visible")
        assert await card.get_by_label("Alıcı telefonları").input_value() == "+15550102030"
        assert await page.get_by_label("Operasyon mesajı").count() == 1
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await page.screenshot(path="/tmp/sales-workflow-outreach-mobile.png", full_page=True)
        assert not errors, errors
        print(json.dumps({"checks": ["guided preparation", "connection failure preserves fields", "reload persistence", "pause resume", "single composer", "mobile no overflow", "no JavaScript errors"]}))
        await browser.close()


asyncio.run(main())
