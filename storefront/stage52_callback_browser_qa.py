#!/usr/bin/env python3
"""Read-only mobile and desktop acceptance of removed callback UI."""
import json,os
from playwright.sync_api import sync_playwright
OUT='storefront-stage52-callback'
os.makedirs(OUT,exist_ok=True)
ROOT='https://profikompany.ru'
PAGES=[('home','/'),('cart','/order/'),('product','/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/')]
results=[]
with sync_playwright() as pw:
 browser=pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
 for width in (390,1365):
  mobile=width<768
  ctx=browser.new_context(viewport={'width':width,'height':844},is_mobile=mobile,has_touch=mobile,device_scale_factor=1,locale='ru-RU')
  page=ctx.new_page()
  for label,path in PAGES:
   row={'mode':'mobile' if mobile else 'desktop','page':label,'path':path}
   try:
    response=page.goto(ROOT+path,wait_until='domcontentloaded',timeout=60000)
    page.wait_for_timeout(750)
    row.update(page.evaluate("""() => {
      const links=[...document.querySelectorAll('a[href^="tel:"]')].map(e=>e.getAttribute('href')||'');
      const body=document.body?.textContent||'';
      return {
        callbacks:document.querySelectorAll('.phones__link--callback,a[href="#callback-modal"],a[href*="easyfeedback"]').length,
        modals:document.querySelectorAll('#callback-modal').length,
        callbackLabel:body.includes('Заказать звонок') || body.includes('Форма обратной связи'),
        spb:links.some(x=>x.includes('812') && x.includes('2449264')),
        msk:links.some(x=>x.includes('499') && x.includes('6776332')),
        pageWidth:document.documentElement.scrollWidth,
        viewport:window.innerWidth,
        cartForm:!!document.querySelector('form#s-product-form .product-add input[type="submit"]')
      };
    }"""))
    row['status']=response.status if response else None
    row['passed']=bool(row['status']==200 and row['callbacks']==0 and row['modals']==0 and not row['callbackLabel'] and row['spb'] and row['msk'] and row['pageWidth']<=row['viewport']+3 and (label!='product' or row['cartForm']))
    if label=='home':page.screenshot(path=OUT+'/home-'+str(width)+'.png',full_page=False)
   except Exception as exc:
    row['passed']=False
    row['error']=type(exc).__name__+': '+str(exc).splitlines()[0][:240]
   print('STAGE52_BROWSER='+json.dumps(row,ensure_ascii=False),flush=True)
   results.append(row)
  ctx.close()
 browser.close()
with open(OUT+'/report.json','w',encoding='utf-8') as f:json.dump(results,f,ensure_ascii=False,indent=2)
if not all(r['passed'] for r in results):
 raise SystemExit('Callback browser acceptance failed; rollback required')
