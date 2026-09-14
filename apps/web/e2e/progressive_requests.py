"""Shared request list and review against the real local API; no external messages."""
import asyncio,json,os
from pathlib import Path
from playwright.async_api import async_playwright
BASE=os.environ.get('E2E_WEB_URL','http://localhost:53010')
async def main():
    account=json.loads(Path('/tmp/sales-workflow-e2e-account.json').read_text())
    fixture=json.loads(Path('/tmp/sales-request-browser-fixture.json').read_text())
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={'width':390,'height':844})
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto(BASE+'/tr')
        for label,key in [('Şirket kodu','slug'),('E-posta','email'),('Şifre','password')]:
            await page.get_by_label(label,exact=True).fill(account[key])
        await page.get_by_role('button',name='Giriş yap',exact=True).click()
        async def listing():
            await page.get_by_label('Operasyon mesajı').fill('action:Teknik talepler')
            async with page.expect_response(lambda r: r.url.endswith('/turns') and r.request.method == 'POST') as completed:
                await page.get_by_role('button',name='Gönder',exact=True).click()
            response=await completed.value
            assert response.status == 200, await response.text()
            row=(await response.json())['workflows'][-1]
            card=page.locator('#workflow-'+row['id'])
            await card.get_by_label('Kayıtlarda ara').fill(fixture['name'])
            await card.get_by_role('button',name='Kayıtları ara',exact=True).click()
            await card.locator('summary').filter(has_text=fixture['name']).click()
            return card
        card=await listing()
        await card.get_by_role('button',name='İç not ekle',exact=True).click()
        edit=page.get_by_role('region',name='Talebi güncelle',exact=True).last
        await edit.get_by_label('İç not',exact=False).fill('Tarayıcı kabulü: teknik ekip inceleyecek.')
        await edit.get_by_role('button',name='Devam et',exact=True).click()
        await edit.get_by_role('button',name='Bilgileri düzenle',exact=True).click()
        assert await edit.get_by_label('İç not',exact=False).input_value()=='Tarayıcı kabulü: teknik ekip inceleyecek.'
        await edit.get_by_role('button',name='Devam et',exact=True).click()
        await edit.get_by_role('button',name='Talep değişikliğini uygula',exact=True).click()
        await edit.get_by_text('Talep güncellendi. Müşteriye mesaj gönderilmedi.',exact=True).wait_for()
        card=await listing()
        await card.get_by_role('button',name='Durumu değiştir',exact=True).click()
        edit=page.get_by_role('region',name='Talebi güncelle',exact=True).last
        await edit.get_by_label('Yeni durum',exact=False).select_option('in_review')
        await edit.get_by_role('button',name='Devam et',exact=True).click()
        await edit.get_by_role('button',name='Talep değişikliğini uygula',exact=True).click()
        await edit.get_by_text('Talep güncellendi. Müşteriye mesaj gönderilmedi.',exact=True).wait_for()
        card=await listing()
        await card.locator('details').filter(has=page.locator('summary').filter(has_text=fixture['name'])).locator('dd').filter(has_text='İnceleniyor').wait_for()
        await card.get_by_role('button',name='Talep ayrıntıları',exact=True).click()
        detail=page.get_by_role('region',name='Talep ayrıntıları',exact=True).last
        await detail.locator('summary').filter(has_text=fixture['name']).click()
        await detail.get_by_text('Tarayıcı kabulü: teknik ekip inceleyecek.',exact=True).wait_for()
        await detail.locator('summary').filter(has_text='drawing.pdf').click()
        async with page.expect_download() as received:
            await detail.get_by_role('link',name='Dosyayı indir',exact=True).click()
        download=await received.value
        downloaded=await download.path()
        assert Path(downloaded).read_bytes()==b'%PDF-1.4\nsynthetic download acceptance'
        assert await page.get_by_label('Operasyon mesajı').count()==1
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await detail.scroll_into_view_if_needed()
        await page.screenshot(path='/tmp/sales-workflow-requests-mobile.png',full_page=True)
        assert not errors,errors
        print(json.dumps({'checks':['shared request search','record note action','back preserves note','explicit review commit','record status action','updated status visible','shared detail notes','authenticated file download','one composer','mobile no overflow','no JS errors'],'Meta':'not called'}))
        await browser.close()
asyncio.run(main())
