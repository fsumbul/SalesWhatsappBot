"""Actual local record navigation and last-owner protection in Chromium."""
import asyncio,json,os
from pathlib import Path
from uuid import uuid4
from playwright.async_api import async_playwright
BASE=os.environ.get("E2E_WEB_URL","http://localhost:53010")

async def main():
    account=json.loads(Path(os.environ.get("E2E_ACCOUNT_FILE","/tmp/sales-workflow-e2e-account.json")).read_text())
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={"width":1440,"height":1000})
        errors=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        await page.goto(BASE+"/tr")
        await page.get_by_label("Şirket kodu").fill(account["slug"])
        await page.get_by_label("E-posta",exact=True).fill(account["email"])
        await page.get_by_label("Şifre",exact=True).fill(account["password"])
        await page.get_by_role("button",name="Giriş yap",exact=True).click()
        async def menu(label):
            await page.get_by_text("Hesap",exact=True).click()
            await page.get_by_role("button",name=label,exact=True).click()
        await menu("Asistanlar")
        listing=page.get_by_role("region",name="Asistanlar",exact=True).filter(has=page.get_by_text("Bilgi bekliyor",exact=True)).last
        await listing.get_by_role("button",name="Asistan oluştur",exact=True).click()
        creation=page.get_by_role("region",name="Asistan oluştur",exact=True)
        name="Record "+uuid4().hex[:8]
        slug=name.lower().replace(" ","-")
        await creation.get_by_label("Asistan adı").fill(name)
        await creation.get_by_label("Asistan kodu").fill(slug)
        await creation.get_by_role("button",name="Devam et",exact=True).click()
        await creation.get_by_role("button",name="Asistanı oluştur",exact=True).click()
        await page.get_by_text(name+" asistanı oluşturuldu.",exact=False).wait_for()
        await menu("Asistanlar")
        listing=page.get_by_role("region",name="Asistanlar",exact=True).filter(has=page.get_by_text("Bilgi bekliyor",exact=True)).last
        await listing.get_by_label("Kayıtlarda ara").fill(name)
        await listing.get_by_role("button",name="Kayıtları ara",exact=True).click()
        await listing.locator("summary").filter(has_text=name).click()
        search_input_id=await listing.get_by_label("Kayıtlarda ara").get_attribute("id")
        await listing.get_by_role("button",name="Bilgi ekle",exact=True).click()
        configuration=page.get_by_role("region",name="Şirket bilgilerini değiştir",exact=True)
        assert await configuration.get_by_label("Asistan",exact=False).input_value()==slug
        assert await page.locator("[id='"+search_input_id+"']").input_value()==name
        assert await page.get_by_role("navigation",name="Asistan bölümleri").count()==0
        await menu("Ekip ve yetkiler")
        team=page.get_by_role("region",name="Ekip ve yetkiler",exact=True)
        await team.locator("summary").filter(has_text=account["email"]).click()
        await team.get_by_role("button",name="Erişimi değiştir",exact=True).click()
        member=page.get_by_role("region",name="Ekip erişimini değiştir",exact=True)
        await member.get_by_label("Yeni yetki").select_option("viewer")
        await member.get_by_role("button",name="Devam et",exact=True).click()
        await member.get_by_role("button",name="Ekip erişimini güncelle",exact=True).click()
        await member.get_by_role("alert").filter(has_text="Son etkin şirket sahibinin erişimi korunmalı.").wait_for()
        await page.screenshot(path="/tmp/sales-workflow-records-desktop.png",full_page=True)
        await page.set_viewport_size({"width":390,"height":844})
        await page.evaluate("new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert await page.get_by_label("Operasyon mesajı").count()==1
        await page.screenshot(path="/tmp/sales-workflow-records-mobile.png",full_page=True)
        assert not errors,errors
        print(json.dumps({"checks":["account menu opens shared list","create from list","search records","open selected agent workflow","preserve search context","no embedded agent panel","member change preview","last owner error is specific","single composer","mobile no overflow","no JS errors"]}))
        await browser.close()
asyncio.run(main())
