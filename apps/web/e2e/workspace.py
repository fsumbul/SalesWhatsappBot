import asyncio,json,os
BASE_URL = os.environ.get("E2E_WEB_URL", "http://localhost:53000")
PLATFORM_PASSWORD = os.environ["E2E_PLATFORM_PASSWORD"]
from playwright.async_api import async_playwright

async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch()
  page=await browser.new_page(viewport={'width':1440,'height':1000})
  errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  await page.goto(BASE_URL + '/tr')
  await page.get_by_label('Şirket kodu').fill('e2e-platform')
  await page.get_by_label('E-posta',exact=True).fill('admin@example.com')
  await page.get_by_label('Şifre',exact=True).fill(PLATFORM_PASSWORD)
  await page.get_by_role('button',name='Giriş yap',exact=True).click()
  await page.get_by_text('Hesap', exact=True).click()
  await page.get_by_role('button',name='Şirketler',exact=True).click()
  await page.get_by_label('Şirket adı',exact=True).fill('E2E Danışmanlık')
  import time
  slug='e2e-consulting-'+str(int(time.time()))
  await page.get_by_label('Şirket kodu',exact=True).fill(slug)
  await page.get_by_label('Şirket sahibinin e-postası').fill('owner@example.com')
  await page.get_by_role('button',name='Davet bağlantısı oluştur').click()
  link=page.get_by_label('Davet bağlantısı · 7 gün geçerli')
  await link.wait_for()
  invite=await link.input_value()
  await page.screenshot(path='/tmp/sales-platform.png',full_page=True)
  context=await browser.new_context(viewport={'width':1440,'height':1000})
  owner=await context.new_page()
  await owner.goto(invite)
  await owner.get_by_label('Ad soyad').fill('E2E Owner')
  await owner.get_by_label('Şifre',exact=True).fill('Local-owner-password-123')
  await owner.get_by_role('button',name='Daveti kabul et').click()
  await owner.get_by_label('E-posta',exact=True).fill('owner@example.com')
  await owner.get_by_role('button',name='Giriş yap',exact=True).click()
  await owner.get_by_text('Hesap', exact=True).click()
  await owner.get_by_role('button', name='Asistanlar', exact=True).click()
  await owner.get_by_label('Ad',exact=True).fill('Danışmanlık asistanı')
  await owner.get_by_label('Kod',exact=True).fill('consulting-assistant')
  await owner.get_by_role('button',name='Oluştur',exact=True).click()
  await owner.get_by_label('Şirket adı',exact=True).wait_for()
  await owner.get_by_role('button',name='Değişikliği önizle').click()
  await owner.get_by_role('button',name='Taslağa uygula').click()
  await owner.get_by_text('Onaylı bilgi ekle', exact=True).click()
  await owner.get_by_label('Bilgi kodu', exact=True).fill('services')
  await owner.get_by_label('Müşteriye gösterilecek onaylı metin', exact=True).fill('Şirketlere yönetim danışmanlığı sunuyoruz.')
  await owner.get_by_label('Bilginin kaynağı', exact=True).fill('E2E şirket sahibi')
  await owner.get_by_role('button', name='Bilgiyi önizle', exact=True).click()
  await owner.get_by_role('button', name='Taslağa uygula', exact=True).click()
  await owner.reload()
  await owner.get_by_text('Hesap', exact=True).click()
  await owner.get_by_role('button', name='Asistanlar', exact=True).click()
  await owner.get_by_label('Şirket adı',exact=True).wait_for()
  assert await owner.get_by_label('Şirket adı',exact=True).input_value()=='E2E Danışmanlık'
  await owner.screenshot(path='/tmp/sales-company.png',full_page=True)
  await owner.get_by_role('navigation',name='Asistan bölümleri').get_by_role('button',name='Müşteri testi',exact=True).click()
  await owner.get_by_label('Müşteri mesajı',exact=True).fill('Hangi hizmetleri sunuyorsunuz?')
  await owner.locator('form').filter(has=owner.get_by_label('Müşteri mesajı',exact=True)).get_by_role('button',name='Gönder',exact=True).click()
  await owner.get_by_text('Model yanıtı alınamadı veya doğrulanamadı',exact=False).wait_for()
  await owner.screenshot(path='/tmp/sales-model-outage.png',full_page=True)
  cookies=await context.cookies()
  assert all(c['httpOnly'] and c['secure'] for c in cookies if c['name'].startswith('ashira_'))
  assert await owner.get_by_role('button',name='Şirketler',exact=True).count()==0
  old_refresh = next(c['value'] for c in cookies if c['name']=='ashira_refresh')
  await context.clear_cookies(name='ashira_access')
  statuses = await owner.evaluate("Promise.all(Array.from({length: 6}, () => fetch('/api/platform/auth/me').then(r => r.status)))")
  assert statuses == [200] * 6, statuses
  new_cookies = await context.cookies()
  assert next(c['value'] for c in new_cookies if c['name']=='ashira_refresh') != old_refresh
  rejected = await owner.request.post(BASE_URL + '/api/platform/agents', headers={'Origin':'https://untrusted.example'}, data={'name':'Forbidden','slug':'forbidden'})
  assert rejected.status == 403
  assert await owner.get_by_label('Operasyon mesajı').count() == 1
  assert await owner.get_by_role('button', name='← Sohbete dön', exact=True).count() == 0
  await owner.get_by_label('Operasyon mesajı').fill('action:Talepleri analiz et')
  await owner.get_by_role('button',name='Gönder',exact=True).last.click()
  await owner.get_by_text('Bu kapsamda henüz kayıtlı talep yok.', exact=False).wait_for()
  await owner.set_viewport_size({'width':390,'height':844})
  await owner.screenshot(path='/tmp/sales-mobile.png',full_page=True)
  assert await owner.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
  await owner.get_by_text('Hesap', exact=True).click()
  await owner.get_by_role('button', name='Çıkış yap', exact=True).click()
  await owner.get_by_role('button', name='Giriş yap', exact=True).wait_for()
  assert (await owner.request.get(BASE_URL + '/api/platform/auth/me')).status == 401
  assert not errors,errors
  print(json.dumps({'passed':['platform login','company provisioning','owner invite','tenant login','agent creation','proposal acceptance','reload persistence','HttpOnly Secure cookies','role navigation','mobile no overflow','concurrent refresh single-flight','CSRF rejection','operational chat','logout','approved fact form','visible model outage'],'company':slug}))
  await browser.close()
asyncio.run(main())
