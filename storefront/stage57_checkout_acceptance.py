"""Anonymous verification, no contact input and no order submission."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path('storefront-stage57-evidence');out.mkdir(exist_ok=True)
with sync_playwright()as pw:
 browser=pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
 rows=[]
 for width in (390,1365):
  ctx=browser.new_context(viewport={'width':width,'height':844},is_mobile=width<768,has_touch=width<768,locale='ru-RU');p=ctx.new_page()
  p.route('**/mc.yandex.*/**',lambda r:r.abort());p.add_init_script('window.ym=function(){};')
  p.goto('https://profikompany.ru/dostavka/',wait_until='domcontentloaded',timeout=45000)
  assert p.locator('h1').count()==1
  assert p.locator('h2').evaluate_all('(xs)=>xs.some(x=>x.textContent.trim()==="Доставка")') is False
  p.screenshot(path=str(out/('delivery-'+str(width)+'.png')),full_page=True)
  p.goto('https://profikompany.ru/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',wait_until='domcontentloaded',timeout=45000)
  buy=p.locator('.megapolis-mobile-buy__button')if width<768 else p.locator('#s-product-form .product-add input[type=submit]')
  buy.first.click();p.wait_for_url('**/order/**',wait_until='domcontentloaded',timeout=45000)
  assert p.locator('#js-order-form').count()==1 and 'РС42' in p.locator('body').inner_text()
  assert p.locator('#megapolis-delivery-notice').count()==1
  assert p.locator('.cart-help').is_visible()==(width>=768)
  phones=p.locator('a[href^="tel:"]').evaluate_all('(xs)=>xs.filter(x=>x.getBoundingClientRect().width>0).map(x=>x.href).join(" ")')
  assert '2449264' in phones and '6776332' in phones
  assert not p.evaluate('document.documentElement.scrollWidth>innerWidth+3')
  p.screenshot(path=str(out/('cart-'+str(width)+'.png')),full_page=True)
  rows.append({'width':width,'native_nonempty_checkout':True,'help_visible':width>=768,'paired_phones':True,'delivery_heading_single':True,'order_submitted':False})
  ctx.close()
 browser.close()
(out/'acceptance.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
print('STAGE57_ACCEPTANCE='+json.dumps({'passed':True,'widths':2,'orders_created':0}))
