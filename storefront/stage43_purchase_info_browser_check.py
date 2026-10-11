#!/usr/bin/env python3
"""Strict anonymous mobile/desktop verification of deduplicated purchase information."""
import json,os
from playwright.sync_api import sync_playwright
ORIGIN='https://profikompany.ru'
cases=[
 ('categorized','/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/'),
 ('uncategorized','/stul-sevyn-bukle-kofeynyy-2-sht/'),
]
results=[]
with sync_playwright() as pw:
 browser=pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
 for mode in ('mobile','desktop'):
  mobile=(mode=='mobile')
  for kind,path in cases:
   ctx=browser.new_context(
    viewport={'width':390 if mobile else 1365,'height':844 if mobile else 900},
    device_scale_factor=1,is_mobile=mobile,has_touch=mobile,locale='ru-RU')
   page=ctx.new_page()
   errors=[]
   page.on('pageerror',lambda e:errors.append(str(e)[:140]))
   result={'mode':mode,'kind':kind,'path':path}
   try:
    resp=page.goto(ORIGIN+path,wait_until='domcontentloaded',timeout=60000)
    page.wait_for_timeout(900)
    result['status']=resp.status if resp else None
    fields={
     'trust':'.product-page__cart > .megapolis-purchase-trust',
     'repeat_text':'.product-page__cart > .product-page__buy-help',
     'repeat_icons':'.product-page__cart > .features.features--inline.features--circle',
     'broken_consultation':'.megapolis-purchase-consultation',
     'buy_form':'form#s-product-form input[type="submit"]',
     'price':'.product-price',
     'product_description':'.product-tabs',
    }
    result['counts']={key:page.locator(css).count() for key,css in fields.items()}
    result['no_horizontal_scroll']=page.evaluate(
      'document.documentElement.scrollWidth <= window.innerWidth + 3')
    result['trust_phone_link']=page.locator(
      '.megapolis-purchase-trust a[href="tel:+78122449264"]').count()
    result['trust_delivery_link']=page.locator(
      '.megapolis-purchase-trust a[href="/dostavka/"]').count()
    result['trust_payment_link']=page.locator(
      '.megapolis-purchase-trust a[href="/oplata/"]').count()
    result['page_errors']=errors
    counts=result['counts']
    normal=result['status']==200 and counts['buy_form']>=1 and counts['price']>=1 and result['no_horizontal_scroll'] and not errors
    if kind=='categorized':
     result['passed']=bool(normal and counts['trust']==1 and
       counts['repeat_text']==0 and counts['repeat_icons']==0 and
       counts['broken_consultation']==0 and result['trust_phone_link']==1 and
       result['trust_delivery_link']==1 and result['trust_payment_link']==1)
    else:
     result['passed']=bool(normal and counts['trust']==0 and counts['repeat_text']==0
       and counts['repeat_icons']==1 and counts['broken_consultation']==0)
   except Exception as exc:
    result['error']=type(exc).__name__+': '+str(exc).splitlines()[0][:160]
    result['passed']=False
   print('STAGE43_FINAL='+json.dumps(result,ensure_ascii=False),flush=True)
   results.append(result)
   ctx.close()
 browser.close()
summary={'total':len(results),'passed':sum(x['passed'] for x in results)}
print('STAGE43_FINAL_SUMMARY='+json.dumps(summary,ensure_ascii=False),flush=True)
if os.getenv('STRICT_CONVERSION_CHECK')=='1' and not all(x['passed'] for x in results):
 raise SystemExit(2)
