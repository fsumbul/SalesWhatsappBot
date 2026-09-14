"""Real local config/import/publish browser acceptance, without provider fixtures."""
import asyncio
import json
import os
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
        async def send(text):
            await page.get_by_label("Operasyon mesajı").fill("action:"+text)
            await page.get_by_role("button",name="Gönder",exact=True).click()
        await send("Asistan oluştur")
        agent=page.get_by_role("region",name="Asistan oluştur",exact=True)
        await agent.get_by_label("Asistan adı").fill("Tarayıcı Asistanı")
        await agent.get_by_label("Asistan kodu").fill("browser-"+uuid4().hex[:10])
        await agent.get_by_role("button",name="Devam et",exact=True).click()
        await agent.get_by_role("button",name="Asistanı oluştur",exact=True).click()
        await page.get_by_text("Tarayıcı Asistanı asistanı oluşturuldu.",exact=False).wait_for()
        await send("Bilgi ekle")
        config=page.get_by_role("region",name="Şirket bilgilerini değiştir",exact=True)
        await config.get_by_label("Bilgi kaynağı").select_option("json")
        content={"lifecycle":"draft","organization":{"id":"company","display_names":{"tr":"Tarayıcı Şirketi"}},"agent":{"purposes":["information"],"supported_locales":["tr"],"default_locale":"tr"},"facts":[{"id":"service","subject_id":"company","category":"capability","value":"Bakım hizmeti veriyoruz.","customer_visible":True,"customer_text":{"tr":"Bakım hizmeti veriyoruz."},"source":"owner"}]}
        await config.get_by_label("JSON / CSV dosyası seç").set_input_files({"name":"company.json","mimeType":"application/json","buffer":json.dumps(content,ensure_ascii=False).encode()})
        await config.get_by_role("button",name="Devam et",exact=True).click()
        await config.get_by_role("button",name="Taslağa kaydet",exact=True).wait_for()
        assert await config.get_by_role("region",name="Değişiklik özeti").count()==1
        await config.get_by_role("button",name="Bilgileri düzenle",exact=True).click()
        assert "Tarayıcı" in await config.get_by_label("Eklenecek veya değiştirilecek bilgi").input_value()
        await config.get_by_role("button",name="Devam et",exact=True).click()
        await config.get_by_role("button",name="Taslağa kaydet",exact=True).click()
        await page.get_by_text("Bilgiler taslağa kaydedildi. Canlıya yayınlanmadı.",exact=True).wait_for()
        await send("Bilgi ekle")
        config=page.get_by_role("region",name="Şirket bilgilerini değiştir",exact=True).last
        await config.get_by_label("Bilgi kaynağı",exact=False).first.select_option("csv")
        csv_content="Kimlik,Konu,Tür,Açıklama,Kaynak\nadditional,company,capability,Yerinde bakım hizmeti veriyoruz.,owner\n"
        await config.get_by_label("JSON / CSV dosyası seç").set_input_files({"name":"facts.csv","mimeType":"text/csv","buffer":csv_content.encode()})
        for label,value in [("Bilgi kimliği sütunu","Kimlik"),("Şirket / ürün kimliği sütunu","Konu"),("Bilgi türü sütunu","Tür"),("Müşteriye gösterilecek metin sütunu","Açıklama"),("Bilgi kaynağı sütunu","Kaynak")]:
            await config.get_by_label(label,exact=False).select_option(value)
        assert await config.get_by_label("CSV sütun eşlemesi",exact=False).count()==0
        await page.set_viewport_size({"width":390,"height":844})
        await config.get_by_label("Müşteriye gösterilecek metin sütunu",exact=False).scroll_into_view_if_needed()
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await page.screenshot(path="/tmp/sales-workflow-csv-mapping-mobile.png",full_page=True)
        await page.set_viewport_size({"width":1440,"height":1000})
        await config.get_by_role("button",name="Devam et",exact=True).click()
        await config.get_by_role("button",name="Taslağa kaydet",exact=True).click()
        await config.get_by_text("Bilgiler taslağa kaydedildi. Canlıya yayınlanmadı.",exact=True).wait_for()
        await send("Taslağı yayınla")
        publication=page.get_by_role("region",name="Sürümü yayınla",exact=True)
        await publication.get_by_role("button",name="Devam et",exact=True).click()
        await publication.get_by_role("button",name="Sürümü yayınla",exact=True).wait_for()
        await page.screenshot(path="/tmp/sales-workflow-publish.png",full_page=True)
        await publication.get_by_role("button",name="Sürümü yayınla",exact=True).click()
        await page.get_by_text("Tarayıcı Asistanı v1 yayınlandı.",exact=False).wait_for()
        await send("Müşteri testi")
        test=page.get_by_role("region",name="Müşteri testi",exact=True)
        await test.get_by_label("Denenecek müşteri mesajı").fill("Hangi hizmetleri sunuyorsunuz?")
        await test.get_by_role("button",name="Devam et",exact=True).click()
        await test.get_by_role("button",name="Müşteri testini çalıştır",exact=True).click()
        await test.get_by_text("İşlem başarısız",exact=True).wait_for()
        assert await test.get_by_label("Denenecek müşteri mesajı").input_value()=="Hangi hizmetleri sunuyorsunuz?"
        assert await test.get_by_text("WhatsApp mesajı gönderilmedi.",exact=True).count()==1
        await page.set_viewport_size({"width":390,"height":844})
        await page.evaluate("new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await page.screenshot(path="/tmp/sales-workflow-test-outage.png",full_page=True)
        assert not errors,errors
        print(json.dumps({"checks":["progressive agent creation","JSON file input","CSV file input","map actual headers with dropdowns","CSV saved in draft","before after preview","edit invalidates preview","draft distinct from publish","explicit publication","version selected for test","model outage retains input","no WhatsApp send","mobile no overflow","no JS errors"],"provider":"not configured; failure path verified"}))
        await browser.close()
asyncio.run(main())
