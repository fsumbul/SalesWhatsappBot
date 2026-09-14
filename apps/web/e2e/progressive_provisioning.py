"""Real synthetic company -> owner invitation acceptance -> first agent workflow.
No model or external messaging; platform account must be a local test account.
"""
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from playwright.async_api import async_playwright

BASE = os.environ.get("E2E_WEB_URL", "http://localhost:53010")


async def main():
    account = json.loads(Path(os.environ.get("E2E_PLATFORM_ACCOUNT_FILE", "/tmp/sales-owner-browser-account.json")).read_text())
    suffix = uuid4().hex[:10]
    slug, company = "provision-" + suffix, "E2E Yeni Şirket " + suffix
    email, password = "owner-" + suffix + "@example.com", "Local-owner-" + uuid4().hex
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(permissions=["clipboard-read", "clipboard-write"])
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(BASE + "/tr")
        for label, key in [("Şirket kodu", "slug"), ("E-posta", "email"), ("Şifre", "password")]:
            await page.get_by_label(label, exact=True).fill(account[key])
        await page.get_by_role("button", name="Giriş yap", exact=True).click()
        await page.get_by_text("Hesap", exact=True).click()
        await page.get_by_role("button", name="Şirketler", exact=True).click()
        listing = page.get_by_role("region", name="Şirketler", exact=True).last
        await listing.get_by_role("button", name="Şirket oluştur", exact=True).click()
        card = page.get_by_role("region", name="Şirket oluştur", exact=True).last
        for label, value in [("Şirket adı", company), ("Şirket kodu", slug), ("Şirket sahibinin e-postası", email)]:
            await card.get_by_label(label).fill(value)
        await card.get_by_role("button", name="Devam et", exact=True).click()
        async with page.expect_response(lambda r: r.url.endswith('/actions') and r.request.method == 'POST' and r.request.post_data_json.get('action') == 'complete') as completed:
            await card.get_by_role("button", name="Şirket ve sahip daveti oluştur", exact=True).click()
        response = await completed.value
        assert response.status == 200
        result = (await response.json())["result"]
        assert result["outcome"] == "company_created"
        await card.get_by_text("WhatsApp bağlantısı kurulmadı.", exact=False).wait_for()
        await card.get_by_role("button", name="Davet bağlantısını kopyala", exact=True).click()
        invite = await page.evaluate("navigator.clipboard.readText()")
        assert result["token"] in invite and invite.startswith(BASE + '/tr?invite=')
        owner_context = await browser.new_context(viewport={"width": 390, "height": 844})
        owner = await owner_context.new_page()
        owner.on("pageerror", lambda e: errors.append(str(e)))
        await owner.goto(invite)
        assert await owner.get_by_label("Şirket kodu").count() == 0
        await owner.get_by_label("Ad soyad").fill("Yeni Şirket Sahibi")
        await owner.get_by_label("Şifre", exact=True).fill(password)
        await owner.get_by_role("button", name="Daveti kabul et", exact=True).click()
        await owner.get_by_text("Davet kabul edildi.", exact=False).wait_for()
        assert 'invite=' not in owner.url
        await owner.get_by_label("Şirket kodu").fill(slug)
        await owner.get_by_label("E-posta", exact=True).fill(email)
        await owner.get_by_label("Şifre", exact=True).fill(password)
        await owner.get_by_role("button", name="Giriş yap", exact=True).click()
        await owner.get_by_label("Operasyon mesajı").wait_for()
        me = await owner.request.get(BASE + '/api/platform/auth/me')
        identity = await me.json()
        assert me.status == 200 and identity['user']['role'] == 'tenant_owner' and identity['tenant']['id'] == result['tenant_id']
        await owner.get_by_text("Hesap", exact=True).click()
        assert await owner.get_by_role("button", name="Şirketler", exact=True).count() == 0
        await owner.get_by_role("button", name="Asistanlar", exact=True).click()
        agents = owner.get_by_role("region", name="Asistanlar", exact=True).last
        await agents.get_by_role("button", name="Asistan oluştur", exact=True).click()
        agent = owner.get_by_role("region", name="Asistan oluştur", exact=True).last
        await agent.get_by_label("Asistan adı").fill("İlk asistan")
        await agent.get_by_label("Asistan kodu").fill("first-agent")
        await agent.get_by_role("button", name="Devam et", exact=True).click()
        await agent.get_by_role("button", name="Asistanı oluştur", exact=True).click()
        await agent.get_by_text("İlk asistan asistanı oluşturuldu.", exact=False).wait_for()
        assert await owner.get_by_label("Operasyon mesajı").count() == 1
        assert await owner.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await owner.screenshot(path='/tmp/progressive-provisioning-mobile.png', full_page=True)
        # Reload restores the newly created owner's own conversation and result.
        await owner.reload()
        await owner.get_by_role("button", name="Sohbet geçmişi", exact=True).click()
        await owner.get_by_role("dialog").get_by_role("button", name="Asistanlar", exact=True).first.click()
        await owner.get_by_text("İlk asistan asistanı oluşturuldu.", exact=False).wait_for()
        assert not errors, errors
        print(json.dumps({"checks": ["company created from common records", "explicit company review", "clipboard invitation link", "invitation accepted in separate browser context", "token removed from URL", "new owner login and role", "platform action hidden", "first agent through common workflow", "single composer", "mobile no overflow", "owner history persists", "no JS errors"], "model": "not called", "Meta": "not called"}))
        await browser.close()


asyncio.run(main())
