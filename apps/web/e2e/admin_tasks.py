"""Browser -> local API -> actual model -> PostgreSQL; synthetic messages only.

Seed with apps/api/scripts/verify_admin_tasks.py. This never clicks a send review.
"""

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright


async def main():
    base = os.environ.get("E2E_WEB_URL", "http://localhost:53010")
    if urlparse(base).hostname not in {"localhost", "127.0.0.1"}:
        raise SystemExit("Only a local test website is allowed")
    account = json.loads(Path(os.environ.get("E2E_ACCOUNT_FILE", "/tmp/sales-admin-task-account.json")).read_text())
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        page.set_default_timeout(300000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(base + "/tr")
        await page.get_by_label("Şirket kodu").fill(account["slug"])
        await page.get_by_label("E-posta", exact=True).fill(account["email"])
        await page.get_by_label("Şifre", exact=True).fill(account["password"])
        await page.get_by_role("button", name="Giriş yap", exact=True).click()
        await page.get_by_label("Operasyon mesajı").wait_for()
        question = "+1 (555) 010-2030 ne konuşmuş, son mesajımız okunmuş mu?"
        if os.environ.get("E2E_REUSE_LAST") == "1":
            # Continue UI verification of the real model turn already persisted by a prior run.
            sessions = await (await page.request.get(base + "/api/platform/admin-chat/sessions")).json()
            sid = next(s["id"] for s in sessions if s["title"] == question)
            body = (await (await page.request.get(base + f"/api/platform/admin-chat/sessions/{sid}/messages")).json())[-1]
            await page.get_by_role("button", name="Sohbet geçmişi", exact=True).click()
            await page.get_by_role("dialog").get_by_role("button", name=question, exact=False).first.click()
        else:
            await page.get_by_label("Operasyon mesajı").fill(question)
            async with page.expect_response(lambda r: r.url.endswith("/turns") and r.request.method == "POST") as completed:
                await page.get_by_role("button", name="Gönder", exact=True).click()
            response = await completed.value
            assert response.status == 200
            body = await response.json()
        task = next(c for c in body["cards"] if c["type"] == "task_result")
        assert body["response_source"] == "model"
        assert len(task["outcomes"]) == 2
        assert all(o["status"] == "completed" for o in task["outcomes"])
        result = page.get_by_label("İsteğin sonuçları", exact=True)
        await result.wait_for()
        outcome = result.locator(":scope > details").first
        await outcome.locator(":scope > summary").click()
        for source in await outcome.get_by_text("Dayanak kayıtlar", exact=True).all():
            await source.click()
        await outcome.get_by_text("Mesaj: Döküm kasnak kataloğunu rica ediyorum.", exact=True).first.wait_for()
        assert await page.get_by_label("Operasyon mesajı").count() == 1
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await result.scroll_into_view_if_needed()
        await page.screenshot(path="/tmp/admin-tasks-browser-mobile.png", full_page=True)
        await page.reload()
        await page.get_by_role("button", name="Sohbet geçmişi", exact=True).click()
        await page.get_by_role("dialog").get_by_role("button", name=question, exact=False).first.click()
        await page.get_by_label("İsteğin sonuçları", exact=True).wait_for()
        assert not errors, errors
        print(json.dumps({"passed": True, "reused_real_model_turn": os.environ.get("E2E_REUSE_LAST") == "1", "checks": ["real model multi-goal task", "conversation without technical request", "actual stored read receipt", "evidence expansion", "reload persistence", "mobile containment", "one composer", "no JavaScript errors"], "screenshot": "/tmp/admin-tasks-browser-mobile.png", "external_sends": 0}))
        await browser.close()


asyncio.run(main())
