"""Real browser + API + PostgreSQL. Guided actions; no model or Meta delivery claim."""
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from playwright.async_api import async_playwright, expect

BASE = os.environ.get("E2E_WEB_URL", "http://localhost:53010")


async def main():
    account = json.loads(Path(os.environ.get("E2E_ACCOUNT_FILE", "/tmp/sales-workflow-e2e-account.json")).read_text())
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(BASE + "/tr")
        await page.get_by_label("Şirket kodu").fill(account["slug"])
        await page.get_by_label("E-posta", exact=True).fill(account["email"])
        await page.get_by_label("Şifre", exact=True).fill(account["password"])
        await page.get_by_role("button", name="Giriş yap", exact=True).click()
        await page.get_by_role("button", name="Kişi ekle", exact=True).click()
        await page.get_by_label("Kişi türü").select_option("contact")
        await page.get_by_role("button", name="Devam et", exact=True).click()
        name = page.get_by_role("textbox", name="Ad soyad", exact=True)
        await name.wait_for()
        assert await name.get_attribute("aria-required") == "true"
        await page.get_by_role("button", name="Devam et", exact=True).click()
        await page.locator('[aria-invalid="true"]').first.wait_for()
        for control in await page.locator('input[aria-invalid="true"], select[aria-invalid="true"], textarea[aria-invalid="true"]').all():
            error_id = await control.get_attribute("aria-describedby")
            assert error_id and await page.locator('[id="' + error_id + '"]').inner_text()
        await expect(name).to_be_enabled()
        await name.focus()
        await page.keyboard.type("E2E Deniz")
        await page.keyboard.press("Tab")
        assert await page.evaluate("getComputedStyle(document.activeElement).outlineStyle != 'none'")
        email = "deniz-" + uuid4().hex[:8] + "@example.com"
        await page.get_by_label("E-posta", exact=False).fill(email)
        await page.get_by_role("button", name="Devam et", exact=True).click()
        await page.get_by_role("button", name="Bilgileri düzenle").click()
        assert await page.get_by_label("Ad soyad").input_value() == "E2E Deniz"
        await page.get_by_label("Ad soyad").fill("E2E Deniz Düzeltilmiş")
        await page.get_by_label("Operasyon mesajı").fill("action:Ekip üyesi davet et")
        await page.get_by_role("button", name="Gönder", exact=True).click()
        await page.get_by_role("heading", name="Ekip üyesi davet et", exact=True).wait_for()
        contact = page.get_by_role("region", name="Müşteri / irtibat ekle", exact=True)
        assert await contact.get_by_label("Ad soyad").input_value() == "E2E Deniz Düzeltilmiş"
        await contact.get_by_role("button", name="Kaldığım yerden devam et").click()
        await contact.get_by_role("button", name="Devam et", exact=True).click()
        await contact.get_by_role("button", name="Kişiyi kaydet").wait_for()
        await page.screenshot(path="/tmp/sales-workflow-desktop.png", full_page=True)
        await contact.get_by_role("button", name="Kişiyi kaydet").click()
        await page.get_by_text("E2E Deniz Düzeltilmiş kişi olarak kaydedildi.", exact=True).wait_for()
        await page.reload()
        await page.get_by_role("button", name="Sohbet geçmişi", exact=True).click()
        await page.get_by_role("dialog").get_by_role("button", name="Kişi ekle", exact=True).first.click()
        await page.get_by_text("E2E Deniz Düzeltilmiş kişi olarak kaydedildi.", exact=True).wait_for()
        await page.set_viewport_size({"width":390, "height":844})
        await page.evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert await page.evaluate("document.documentElement.scrollHeight <= innerHeight + 1")
        assert await page.get_by_label("Operasyon mesajı").count() == 1
        await page.screenshot(path="/tmp/sales-workflow-mobile.png", full_page=True)
        assert not errors, errors
        print(json.dumps({"checks": ["required semantics and associated server errors", "keyboard input and visible focus", "guided person choice", "card fields", "back and correction", "save before chat switch", "pause and resume", "complete contact", "reload persisted result", "mobile no overflow", "single composer", "no browser errors"], "model": "not exercised", "Meta": "not called"}))
        await browser.close()


asyncio.run(main())
