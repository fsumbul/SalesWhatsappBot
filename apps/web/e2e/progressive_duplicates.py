"""Duplicate result exposes existing contact details in the shared mobile card."""
import asyncio,json,os
from pathlib import Path
from uuid import uuid4
from playwright.async_api import async_playwright
BASE=os.environ.get('E2E_WEB_URL','http://localhost:53010')
async def main():
    account=json.loads(Path('/tmp/sales-workflow-e2e-account.json').read_text())
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={'width':390,'height':844})
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        await page.goto(BASE+'/tr')
        for label,key in [('Şirket kodu','slug'),('E-posta','email'),('Şifre','password')]:
            await page.get_by_label(label,exact=True).fill(account[key])
        await page.get_by_role('button',name='Giriş yap',exact=True).click()
        email='duplicate-'+uuid4().hex[:8]+'@example.com'
        for name in ['Kayıtlı Deniz','Yeni yazılan ad']:
            await page.get_by_label('Operasyon mesajı').fill('action:Müşteri ekle')
            async with page.expect_response(lambda r:r.url.endswith('/turns') and r.request.method=='POST') as completed:
                await page.get_by_role('button',name='Gönder',exact=True).click()
            response=await completed.value
            assert response.status==200,await response.text()
            row=(await response.json())['workflows'][-1]
            card=page.locator('#workflow-'+row['id'])
            await card.get_by_label('Ad soyad').fill(name)
            await card.get_by_label('E-posta',exact=False).fill(email)
            await card.get_by_role('button',name='Devam et',exact=True).click()
            async with page.expect_response(lambda r:r.url.endswith('/actions') and r.request.method=='POST' and r.request.post_data_json.get('action')=='complete') as saved:
                await card.get_by_role('button',name='Kişiyi kaydet',exact=True).click()
            response=await saved.value
            assert response.status==200,await response.text()
            result=await response.json()
            assert result['result']['outcome']==('created' if name=='Kayıtlı Deniz' else 'duplicate')
        await card.locator('summary').filter(has_text='Kayıtlı Deniz').click()
        await card.get_by_label('Kayıt listesi').get_by_text(email,exact=True).wait_for()
        assert await card.get_by_role('button').count()==0
        assert await page.get_by_label('Operasyon mesajı').count()==1
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await card.scroll_into_view_if_needed()
        await page.screenshot(path='/tmp/sales-workflow-duplicate-mobile.png',full_page=True)
        assert not errors,errors
        print(json.dumps({'checks':['original contact saved','duplicate preserves original name','existing email visible','read-only completed record','one composer','mobile no overflow','no JS errors']}))
        await browser.close()
asyncio.run(main())
