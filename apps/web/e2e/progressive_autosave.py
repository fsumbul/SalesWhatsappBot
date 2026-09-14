"""Real API autosave, including typing and chat switching during a delayed save."""
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
        await page.get_by_role("button", name="Kişi ekle", exact=True).click()
        await page.get_by_label("Kişi türü").select_option("contact")
        await page.get_by_role("button", name="Devam et", exact=True).click()
        card = page.get_by_role("region", name="Müşteri / irtibat ekle", exact=True)
        saving = asyncio.Event()
        delayed = False
        writes = []

        async def delay(route):
            nonlocal delayed
            body = route.request.post_data_json
            if body.get("action") == "update":
                writes.append(body)
                if not delayed:
                    delayed = True
                    saving.set()
                    await asyncio.sleep(0.8)
            await route.continue_()

        await page.route("**/workflows/*/actions", delay)
        await card.get_by_label("Ad soyad").fill("Taslak ilk")
        await asyncio.wait_for(saving.wait(), 10)
        assert await card.get_by_label("Ad soyad").is_enabled()
        await card.get_by_label("Ad soyad").fill("Taslak son karakterler")
        await page.get_by_label("Operasyon mesajı").fill("action:Ekip üyesi davet et")
        await page.get_by_role("button", name="Gönder", exact=True).click()
        await page.get_by_role("heading", name="Ekip üyesi davet et", exact=True).wait_for()
        assert await card.get_by_label("Ad soyad").input_value() == "Taslak son karakterler"
        assert await card.count() == 1
        assert await card.evaluate("el => !!(el.compareDocumentPosition(document.querySelector('[data-turn-sequence=\"2\"]')) & Node.DOCUMENT_POSITION_FOLLOWING)")
        assert len(writes) >= 2, writes
        assert writes[-1]["fields"]["name"] == "Taslak son karakterler"
        await page.reload()
        await page.get_by_role("button", name="Sohbet geçmişi", exact=True).click()
        await page.get_by_role("dialog").get_by_role("button", name="Kişi ekle", exact=True).first.click()
        card = page.get_by_role("region", name="Müşteri / irtibat ekle", exact=True)
        assert await card.get_by_label("Ad soyad").input_value() == "Taslak son karakterler"
        assert await card.evaluate("el => !!(el.compareDocumentPosition(document.querySelector('[data-turn-sequence=\"2\"]')) & Node.DOCUMENT_POSITION_FOLLOWING)")
        await card.get_by_role("button", name="Kaldığım yerden devam et").click()
        await card.get_by_label("Ad soyad").fill("Sadece yazarak kaydedildi")
        await card.get_by_text("Kaydedildi", exact=True).wait_for()
        await page.reload()
        await page.get_by_role("button", name="Sohbet geçmişi", exact=True).click()
        await page.get_by_role("dialog").get_by_role("button", name="Kişi ekle", exact=True).first.click()
        assert await card.get_by_label("Ad soyad").input_value() == "Sadece yazarak kaydedildi"
        await page.unroute("**/workflows/*/actions", delay)
        dropped = False
        attempts = []

        async def drop_response(route):
            nonlocal dropped
            body = route.request.post_data_json
            if body.get("action") == "update":
                attempts.append(body)
                if not dropped:
                    dropped = True
                    await route.fetch()  # Commit reaches the actual API; browser loses its response.
                    await route.abort("failed")
                    return
            await route.continue_()

        await page.route("**/workflows/*/actions", drop_response)
        await card.get_by_label("Ad soyad").fill("Yanıt kayboldu ama kayıt korundu")
        await card.get_by_role("button", name="Aynı işlemi yeniden dene", exact=True).click()
        await card.get_by_text("Kaydedildi", exact=True).wait_for()
        assert len(attempts) == 2, attempts
        assert attempts[0] == attempts[1], attempts
        assert await card.get_by_label("Ad soyad").input_value() == "Yanıt kayboldu ama kayıt korundu"
        await card.get_by_label("Ad soyad").fill("Çıkıştan önce kaydedildi")
        await page.get_by_text("Hesap", exact=True).click()
        await page.get_by_role("button", name="Çıkış yap", exact=True).click()
        await page.get_by_label("Şirket kodu").fill(account["slug"])
        await page.get_by_label("E-posta", exact=True).fill(account["email"])
        await page.get_by_label("Şifre", exact=True).fill(account["password"])
        await page.get_by_role("button", name="Giriş yap", exact=True).click()
        await page.get_by_role("button", name="Sohbet geçmişi", exact=True).click()
        await page.get_by_role("dialog").get_by_role("button", name="Kişi ekle", exact=True).first.click()
        assert await card.get_by_label("Ad soyad").input_value() == "Çıkıştan önce kaydedildi"
        assert not errors, errors
        print(json.dumps({"checks": ["automatic save", "typing while save is pending", "drain latest edits before chat switch", "reload saved draft", "retry identical operation after committed response loss", "save before logout", "card stays beside original turn after new message and reload", "no JavaScript errors"]}))
        await browser.close()


asyncio.run(main())
