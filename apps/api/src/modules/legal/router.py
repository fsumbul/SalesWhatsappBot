"""Public legal pages (privacy policy).

Served at the site root — not under the API prefix — because Meta, app stores
and similar reviewers fetch these URLs anonymously and expect a plain HTML page.
The privacy policy URL is a hard requirement for publishing the Meta app, which
in turn is what makes WhatsApp production webhooks get delivered at all.

The text below is a starting point drafted for KVKK/GDPR expectations. It must
be reviewed by the business owner (and ideally a lawyer) before being treated
as a binding legal statement.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["legal"])

COMPANY_NAME = "Artı Kasnak Asansör Makine İmalat Sanayi Limited Şirketi"
CONTACT_EMAIL = "emrahaks@yahoo.com"
LAST_UPDATED = "15 Ağustos 2026"

_PRIVACY_HTML = f"""<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gizlilik Politikası — {COMPANY_NAME}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    line-height: 1.65; max-width: 760px; margin: 0 auto; padding: 2.5rem 1.25rem 4rem;
    color: #1a1a1a; background: #fff;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ color: #e8e8e8; background: #141414; }}
    a {{ color: #7cb7ff; }}
  }}
  h1 {{ font-size: 1.75rem; margin-bottom: .25rem; }}
  h2 {{ font-size: 1.15rem; margin-top: 2rem; }}
  .meta {{ color: #666; font-size: .9rem; margin-bottom: 2rem; }}
  ul {{ padding-left: 1.25rem; }}
</style>
</head>
<body>
<h1>Gizlilik Politikası</h1>
<p class="meta">{COMPANY_NAME} — Son güncelleme: {LAST_UPDATED}</p>

<p>Bu politika, {COMPANY_NAME} ("Şirket") tarafından WhatsApp Business Platform
üzerinden yürütülen iletişim faaliyetlerinde kişisel verilerin nasıl işlendiğini
açıklar. 6698 sayılı Kişisel Verilerin Korunması Kanunu (KVKK) uyarınca veri
sorumlusu Şirket'tir.</p>

<h2>1. İşlenen veriler</h2>
<ul>
  <li>İletişim bilgileri: telefon numarası, ad ve unvan, e-posta adresi.</li>
  <li>Firma bilgileri: ticaret unvanı, faaliyet alanı, açık kaynaklardan
      derlenen kurumsal iletişim bilgileri.</li>
  <li>Mesajlaşma verileri: WhatsApp üzerinden tarafımıza ilettiğiniz mesajların
      içeriği ile gönderim, teslim ve okunma durumu kayıtları.</li>
</ul>

<h2>2. İşleme amaçları</h2>
<ul>
  <li>Ürün ve hizmetlerimize dair bilgilendirme ve ticari iletişim kurulması.</li>
  <li>Talep, teklif ve şikâyetlerin karşılanması.</li>
  <li>İletişim kayıtlarının hukuki yükümlülükler kapsamında saklanması.</li>
</ul>

<h2>3. Hukuki sebep</h2>
<p>Veriler; ilgili kişinin açık rızası, sözleşmenin kurulması veya ifasıyla
doğrudan ilgili olması, Şirket'in hukuki yükümlülüğünü yerine getirmesi ve
meşru menfaati hukuki sebeplerine dayanılarak işlenir. Ticari elektronik
iletiler, İleti Yönetim Sistemi (İYS) üzerindeki onay kayıtlarına uygun olarak
gönderilir.</p>

<h2>4. Aktarım</h2>
<p>Mesajlaşma altyapısı Meta Platforms Ireland Ltd. tarafından sağlanan WhatsApp
Business Platform üzerinden yürütüldüğünden, iletişim verileri bu hizmetin
sağlanması amacıyla Meta'ya aktarılır ve yurt dışında işlenebilir. Bunun dışında
veriler, yasal olarak yetkili kamu kurumları haricinde üçüncü kişilerle
paylaşılmaz ve pazarlama amacıyla satılmaz.</p>

<h2>5. Saklama süresi</h2>
<p>Veriler, işleme amacının gerektirdiği süre boyunca ve ilgili mevzuatta
öngörülen zamanaşımı süreleri sonuna kadar saklanır; sürenin dolmasıyla
silinir, yok edilir veya anonim hale getirilir.</p>

<h2>6. İletişimi durdurma</h2>
<p>Tarafımızdan gelen WhatsApp mesajlarına <strong>DUR</strong> veya
<strong>STOP</strong> yazarak ticari iletişimi anında durdurabilirsiniz. Talep,
kaydımıza işlenir ve numaranıza yeni ticari ileti gönderilmez.</p>

<h2>7. KVKK kapsamındaki haklarınız</h2>
<p>KVKK'nın 11. maddesi uyarınca; kişisel verilerinizin işlenip işlenmediğini
öğrenme, işlenmişse buna ilişkin bilgi talep etme, işlenme amacını öğrenme,
yurt içinde veya yurt dışında aktarıldığı üçüncü kişileri bilme, eksik veya
yanlış işlenmiş olması hâlinde düzeltilmesini, şartları oluştuğunda silinmesini
veya yok edilmesini isteme ve zararın giderilmesini talep etme haklarına
sahipsiniz.</p>

<h2>8. Veri silme talebi</h2>
<p>Kişisel verilerinizin silinmesini istiyorsanız, aşağıdaki e-posta adresine
"Veri silme talebi" konulu bir ileti gönderin. Talepler en geç 30 gün içinde
sonuçlandırılır.</p>

<h2>9. İletişim</h2>
<p>Veri sorumlusu: {COMPANY_NAME}<br>
E-posta: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></p>
</body>
</html>
"""


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy_policy() -> HTMLResponse:
    return HTMLResponse(content=_PRIVACY_HTML)


@router.get("/data-deletion", response_class=HTMLResponse, include_in_schema=False)
async def data_deletion() -> HTMLResponse:
    """Meta also requires a user-data-deletion instructions URL."""
    html = _PRIVACY_HTML.replace(
        "<h1>Gizlilik Politikası</h1>",
        "<h1>Veri Silme Talimatları</h1>",
    )
    return HTMLResponse(content=html)
