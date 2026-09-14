"""Older guided aliases must enter the current shared UI; no model is mocked here."""
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
        for alias, title in [("asistanlar", "Asistanlar"), ("bilgi ekle", "Şirket bilgilerini değiştir"), ("musteri testi", "Müşteri testi"), ("taslagi yayinla", "Sürümü yayınla"), ("ekip ve yetkiler", "Ekip ve yetkiler")]:
            await page.get_by_label("Operasyon mesajı").fill("action:" + alias)
            await page.get_by_role("button", name="Gönder", exact=True).click()
            card = page.get_by_role("region", name=title, exact=True)
            await card.get_by_text("Bilgi bekliyor", exact=True).wait_for()
            assert await card.get_by_role("button", name="Beklet", exact=True).count() == 1
            assert await page.get_by_role("navigation", name="Asistan bölümleri").count() == 0
        assert await page.get_by_label("Operasyon mesajı").count() == 1
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        print(json.dumps({"checks": ["legacy alias assistant list", "legacy alias configure", "legacy alias customer test", "legacy alias publish", "legacy alias team", "no embedded agent navigation", "one composer", "mobile no overflow", "no JavaScript errors"], "model": "not exercised"}))
        await browser.close()


asyncio.run(main())
