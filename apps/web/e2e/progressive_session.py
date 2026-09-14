"""Real production web/API auth checks, independent of retired workspace panels.
Uses a synthetic tenant account. No model calls or external sends.
"""
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
        context = await browser.new_context()
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(BASE + "/tr")
        await page.get_by_label("Şirket kodu").fill(account["slug"])
        await page.get_by_label("E-posta", exact=True).fill(account["email"])
        await page.get_by_label("Şifre", exact=True).fill(account["password"])
        await page.get_by_role("button", name="Giriş yap", exact=True).click()
        await page.get_by_label("Operasyon mesajı").wait_for()
        cookies = {c["name"]: c for c in await context.cookies() if c["name"].startswith("ashira_")}
        assert set(cookies) == {"ashira_access", "ashira_refresh"}
        assert all(c["httpOnly"] and c["secure"] and c["sameSite"] == "Lax" for c in cookies.values())
        assert "ashira_" not in await page.evaluate("document.cookie")
        await context.clear_cookies(name="ashira_access")
        statuses = await page.evaluate("Promise.all(Array.from({length: 6}, () => fetch('/api/platform/auth/me').then(r => r.status)))")
        assert statuses == [200] * 6, statuses
        refreshed = {c["name"]: c for c in await context.cookies()}
        assert refreshed["ashira_refresh"]["value"] != cookies["ashira_refresh"]["value"]
        rejected = await page.request.post(BASE + "/api/platform/agents", headers={"Origin": "https://untrusted.example"}, data={"name": "Forbidden", "slug": "forbidden"})
        assert rejected.status == 403
        await page.get_by_text("Hesap", exact=True).click()
        assert await page.get_by_role("button", name="Şirketler", exact=True).count() == 0
        await page.get_by_role("button", name="Çıkış yap", exact=True).click()
        await page.get_by_role("button", name="Giriş yap", exact=True).wait_for()
        assert (await page.request.get(BASE + "/api/platform/auth/me")).status == 401
        assert not [c for c in await context.cookies() if c["name"].startswith("ashira_")]
        assert not errors, errors
        print(json.dumps({"checks": ["real tenant login", "HttpOnly Secure SameSite cookies", "cookies unavailable to JavaScript", "six concurrent refresh requests", "refresh token rotation", "cross-origin mutation rejected", "platform action hidden for tenant", "logout clears cookies and access", "no page errors"], "model": "not called", "Meta": "not called"}))
        await browser.close()


asyncio.run(main())
