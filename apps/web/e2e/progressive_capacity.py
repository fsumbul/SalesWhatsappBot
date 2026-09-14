"""Current empty-home/capacity rendering contract; real login, fixture chat responses.
Does not prove Meta capacity retrieval, model behavior or database persistence.
"""
import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright

BASE = os.environ.get('E2E_WEB_URL', 'http://localhost:53010')

async def main():
    account = json.loads(Path(os.environ.get('E2E_ACCOUNT_FILE', '/tmp/sales-workflow-e2e-account.json')).read_text())
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
        messages, errors = [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        cases = [
            {'connected': True, 'meta_available': True, 'meta_limit': 2000, 'local_remaining': 988, 'local_used': 12, 'summary': 'Meta limiti portföyde paylaşılır. Yerel hak ayrı hesaplanır.'},
            {'connected': True, 'meta_available': False, 'local_remaining': 988, 'local_used': 12, 'summary': 'Meta bilgisi alınamadı; yerel hak Meta kapasitesi değildir.'},
            {'connected': False, 'summary': 'WhatsApp bağlantısı kurulmadı.'},
        ]
        async def route(r):
            path = r.request.url.split('admin-chat/')[1]
            if path == 'sessions':
                value = [] if r.request.method == 'GET' else {'id': 'capacity-fixture', 'title': 'WhatsApp limiti'}
            elif path.endswith('/turns'):
                card = {'type': 'capacity', **cases[len(messages) // 2]}
                messages.extend([{'role': 'user', 'text': 'WhatsApp limiti'}, {'role': 'assistant', 'text': 'Kapasite bilgisi', 'cards': [card]}])
                value = {'reply': 'Kapasite bilgisi'}
            elif path.endswith('/messages'):
                value = messages
            elif path.endswith('/workflows'):
                value = []
            else:
                raise AssertionError('Unexpected fixture endpoint: ' + path)
            await r.fulfill(status=200, content_type='application/json', body=json.dumps(value))
        await page.route('**/api/platform/admin-chat/**', route)
        await page.goto(BASE + '/tr')
        for label, key in [('Şirket kodu','slug'),('E-posta','email'),('Şifre','password')]:
            await page.get_by_label(label, exact=True).fill(account[key])
        await page.get_by_role('button', name='Giriş yap', exact=True).click()
        await page.get_by_role('heading', name='Bugün neye bakalım?', exact=True).wait_for()
        assert await page.get_by_label('WhatsApp kapasitesi', exact=True).count() == 0
        assert await page.get_by_role('navigation', name='Asistan bölümleri').count() == 0
        for width in [1440,390]:
            await page.set_viewport_size({'width':width,'height':844})
            await page.evaluate('new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight + 1')
            assert await page.get_by_label('Operasyon mesajı').is_visible()
        for index in range(3):
            await page.get_by_label('Operasyon mesajı').fill('WhatsApp limiti')
            await page.get_by_role('button', name='Gönder', exact=True).click()
            card = page.get_by_label('WhatsApp kapasitesi', exact=True).nth(index)
            await card.wait_for()
            if index < 2:
                await card.get_by_text('988', exact=True).wait_for()
                assert await card.get_by_text('Meta · portföy limiti', exact=True).count() == 1
                assert await card.get_by_text('Uygulama · kalan yerel hak', exact=True).count() == 1
                assert await card.get_by_text('2.000' if index == 0 else 'Alınamadı', exact=True).count() == 1
            else:
                assert await card.get_by_text('Bağlantı kurulmadı', exact=True).count() == 1
                assert await card.get_by_text('Uygulama · kalan yerel hak', exact=True).count() == 0
        assert await page.get_by_label('Operasyon mesajı').count() == 1
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path='/tmp/progressive-capacity-mobile.png',full_page=True)
        assert not errors,errors
        print(json.dumps({'checks':['empty home', 'capacity absent until requested', 'no embedded navigation', 'desktop/mobile containment', 'visible composer', 'Meta/local distinction', 'unavailable Meta not zero', 'disconnected hides metrics', 'one composer', 'no JS errors'], 'chat':'fixture', 'auth':'real', 'Meta':'not called'}))
        await browser.close()

asyncio.run(main())
