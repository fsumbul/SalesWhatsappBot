"""Platform owner invitation UI; synthetic local accounts and no email send."""
import asyncio,json,os
from pathlib import Path
from uuid import uuid4
from playwright.async_api import async_playwright
BASE=os.environ.get('E2E_WEB_URL','http://localhost:53010')
async def main():
    a=json.loads(Path('/tmp/sales-owner-browser-account.json').read_text())
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={'width':390,'height':844})
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto(BASE+'/tr')
        for label,key in [('Şirket kodu','slug'),('E-posta','email'),('Şifre','password')]:
            await page.get_by_label(label,exact=True).fill(a[key])
        await page.get_by_role('button',name='Giriş yap',exact=True).click()
        await page.get_by_text('Hesap',exact=True).click()
        await page.get_by_role('button',name='Şirketler',exact=True).click()
        listing=page.get_by_role('region',name='Şirketler',exact=True).last
        await listing.get_by_label('Kayıtlarda ara').fill(a['target'])
        await listing.get_by_role('button',name='Kayıtları ara',exact=True).click()
        await listing.locator('summary').filter(has_text=a['target']).click()
        await listing.get_by_role('button',name='Sahip daveti hazırla',exact=True).click()
        card=page.get_by_role('region',name='Şirket sahibini davet et',exact=True).last
        email='owner-browser-'+uuid4().hex[:8]+'@example.com'
        await card.get_by_label('Şirket sahibinin e-postası').fill(email)
        await card.get_by_role('button',name='Devam et',exact=True).click()
        await card.get_by_text('E-posta gönderilmez',exact=False).wait_for()
        async with page.expect_response(lambda r:r.url.endswith('/actions') and r.request.method=='POST' and r.request.post_data_json.get('action')=='complete') as received:
            await card.get_by_role('button',name='Sahip davet bağlantısını hazırla',exact=True).click()
        response=await received.value
        assert response.status==200,await response.text()
        result=(await response.json())['result']
        assert result['outcome']=='invitation_ready' and result['email']==email and result['token']
        await card.get_by_text('Davet henüz kabul edilmedi.',exact=False).wait_for()
        assert await page.get_by_label('Operasyon mesajı').count()==1
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await card.scroll_into_view_if_needed()
        await page.screenshot(path='/tmp/sales-workflow-owner-invite-mobile.png',full_page=True)
        assert not errors,errors
        print(json.dumps({'checks':['platform company menu','selected tenant','owner email input','explicit invite review','invitation ready not membership','one composer','mobile no overflow','no JS errors'],'email':'not sent'}))
        await browser.close()
asyncio.run(main())
