"""Anonymous native checkout diagnostics. Never enters contacts or submits an order."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path('stage53-search-probe');out.mkdir(exist_ok=True)
with sync_playwright() as pw:
 b=pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
 for width in (390,1365):
  c=b.new_context(viewport={'width':width,'height':844},is_mobile=width<768,has_touch=width<768,locale='ru-RU');p=c.new_page()
  p.route('**/mc.yandex.*/**',lambda r:r.abort())
  p.add_init_script("window.ym=function(){};")
  p.goto('https://profikompany.ru/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',wait_until='domcontentloaded',timeout=65000)
  button=p.locator('.megapolis-mobile-buy__button') if width<768 else p.locator('#s-product-form .product-add input[type=submit]')
  button.first.click(timeout=15000);p.wait_for_url('**/order/**',wait_until='domcontentloaded',timeout=45000);p.wait_for_timeout(1200)
  info=p.evaluate('''()=>({url:location.href,errorMarkers:[...document.querySelectorAll('#js-order-page .wa-error, #js-order-page .wa-error-text')].map(e=>({tag:e.tagName,class:e.className,text:e.textContent.trim().slice(0,220),rect:{width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height},ancestors:[e,...(function*(){let x=e.parentElement;while(x&&x.id!=='js-order-page'){yield x;x=x.parentElement}})()].slice(0,8).map(x=>({tag:x.tagName,class:x.className,display:getComputedStyle(x).display,visibility:getComputedStyle(x).visibility,ariaHidden:x.getAttribute('aria-hidden')}))})),shippingLabels:[...document.querySelectorAll('#js-order-form label,#js-order-form .wa-type-wrapper')].map(e=>e.textContent.trim()).filter(x=>/890|450|достав|Самовывоз/i.test(x)).slice(0,10)})''')
  p.screenshot(path=str(out/('checkout-'+str(width)+'.png')),full_page=True)
  (out/('checkout-'+str(width)+'.json')).write_text(json.dumps(info,ensure_ascii=False,indent=2))
  print('CHECKOUT_PROBE='+json.dumps({'width':width,**info},ensure_ascii=False),flush=True)
  c.close()
 b.close()
