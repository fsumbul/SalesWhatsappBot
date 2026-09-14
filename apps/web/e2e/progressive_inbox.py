"""Local seeded inbox navigation and preview; never confirms a manual send."""
import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright

BASE = os.environ.get("E2E_WEB_URL", "http://localhost:53010")

async def main():
    account = json.loads(Path(os.environ.get("E2E_ACCOUNT_FILE", "/tmp/sales-workflow-e2e-account.json")).read_text())
    fixture = json.loads(Path("/tmp/sales-inbox-browser-fixture.json").read_text())
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
        await page.get_by_text("Hesap", exact=True).click()
        await page.get_by_role("button", name="Gelen kutusu", exact=True).click()
        listing = page.get_by_role("region", name="Gelen kutusu", exact=True).last
        await listing.get_by_label("Kayıtlarda ara").fill(fixture["name"])
        await listing.get_by_role("button", name="Kayıtları ara", exact=True).click()
        await listing.locator("summary").filter(has_text=fixture["name"]).click()
        await listing.get_by_role("button", name="Konuşmayı aç", exact=True).click()
        conv = page.get_by_role("region", name="Müşteri konuşması", exact=True).last
        await conv.get_by_role("button", name="Yanıt hazırla", exact=True).wait_for()
        await conv.locator("summary").filter(has_text="Müşteri").click()
        await conv.get_by_text("Bakım hizmetiniz var mı?", exact=True).wait_for()
        await conv.get_by_role("button", name="Yanıt hazırla", exact=True).click()
        reply = page.get_by_role("region", name="Müşteriye yanıt hazırla", exact=True).last
        await reply.get_by_label("Gönderilecek yanıt").fill("Talebinizi inceliyoruz.")
        await reply.get_by_role("button", name="Devam et", exact=True).click()
        await reply.get_by_role("button", name="Yanıtı gönder", exact=True).wait_for()
        await reply.get_by_role("button", name="Bilgileri düzenle", exact=True).click()
        assert await reply.get_by_label("Gönderilecek yanıt").input_value() == "Talebinizi inceliyoruz."
        assert await page.get_by_label("Operasyon mesajı").count() == 1
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await reply.scroll_into_view_if_needed()
        await page.screenshot(path="/tmp/sales-workflow-inbox-mobile.png", full_page=True)
        assert not errors, errors
        print(json.dumps({"checks": ["shared inbox menu", "search customer", "open conversation", "read inbound message", "shared reply form", "explicit send review", "back preserves text", "one composer", "mobile no overflow", "no JavaScript errors"], "Meta": "not called"}))
        await browser.close()

asyncio.run(main())
