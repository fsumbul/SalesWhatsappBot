"""Real local rollback UI acceptance against a synthetic published agent."""
import asyncio,json,os
from pathlib import Path
from playwright.async_api import async_playwright
BASE=os.environ.get('E2E_WEB_URL','http://localhost:53010')
async def main():
    account=json.loads(Path('/tmp/sales-workflow-e2e-account.json').read_text())
    fixture=json.loads(Path('/tmp/sales-rollback-browser-fixture.json').read_text())
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={'width':390,'height':844})
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        await page.goto(BASE+'/tr')
        for label,key in [('Şirket kodu','slug'),('E-posta','email'),('Şifre','password')]:
            await page.get_by_label(label,exact=True).fill(account[key])
        await page.get_by_role('button',name='Giriş yap',exact=True).click()
        await page.get_by_label('Operasyon mesajı').fill('action:Sürümler')
        async with page.expect_response(lambda r:r.url.endswith('/turns') and r.request.method=='POST') as received:
            await page.get_by_role('button',name='Gönder',exact=True).click()
        response=await received.value
        assert response.status==200,await response.text()
        row=(await response.json())['workflows'][-1]
        listing=page.locator('#workflow-'+row['id'])
        await listing.get_by_label('Asistan',exact=False).select_option(fixture['slug'])
        await listing.get_by_role('button',name='Kayıtları ara',exact=True).click()
        await listing.locator('summary').filter(has_text='v1').click()
        await listing.get_by_role('button',name='Bu sürüme dön',exact=True).click()
        card=page.get_by_role('region',name='Önceki sürüme dön',exact=True).last
        assert await card.get_by_label('Geri alınacak sürüm').input_value()==fixture['version_id']
        await card.get_by_role('button',name='Devam et',exact=True).click()
        await card.get_by_text('yeni bir LIVE sürüm olarak yayınlanacak',exact=False).wait_for()
        async with page.expect_response(lambda r:r.url.endswith('/actions') and r.request.method=='POST' and r.request.post_data_json.get('action')=='complete') as saved:
            await card.get_by_role('button',name='Seçilen sürümü yeniden yayınla',exact=True).click()
        response=await saved.value
        assert response.status==200,await response.text()
        result=(await response.json())['result']
        assert result['outcome']=='rolled_back' and result['version_id']!=fixture['version_id']
        await card.get_by_text('yeni v2 sürümü olarak yayınlandı',exact=False).wait_for()
        assert await page.get_by_label('Operasyon mesajı').count()==1
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await card.scroll_into_view_if_needed()
        await page.screenshot(path='/tmp/sales-workflow-rollback-mobile.png',full_page=True)
        assert not errors,errors
        print(json.dumps({'checks':['version list selection','exact rollback target','preview describes new LIVE','explicit confirmation','new version identity','published result','one composer','mobile no overflow','no JS errors']}))
        await browser.close()
asyncio.run(main())
