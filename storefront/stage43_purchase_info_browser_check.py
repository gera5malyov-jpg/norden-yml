#!/usr/bin/env python3
"""Anonymous mobile/desktop product purchase information and callback UI check.

Never submits a callback, cart, checkout or an order.
"""
import json,os
from playwright.sync_api import sync_playwright
origin='https://profikompany.ru'
routes=[
 ('categorized','/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/'),
 ('uncategorized','/stul-sevyn-bukle-kofeynyy-2-sht/')
]
strict=os.getenv('STRICT_CONVERSION_CHECK')=='1'
tests=[]
with sync_playwright() as p:
 browser=p.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
 for mode in ['mobile','desktop']:
  for kind,route in routes:
   mobile=mode=='mobile'
   ctx=browser.new_context(viewport={'width':390 if mobile else 1365,'height':844 if mobile else 900},device_scale_factor=1,is_mobile=mobile,has_touch=mobile,locale='ru-RU')
   page=ctx.new_page()
   errors=[];page.on('pageerror',lambda x:errors.append(str(x)[:150]))
   result={'mode':mode,'kind':kind,'url':route}
   try:
    resp=page.goto(origin+route,wait_until='domcontentloaded',timeout=60000)
    page.wait_for_timeout(800)
    result['status']=resp.status if resp else None
    selectors={
     'trust':'.product-page__cart > .megapolis-purchase-trust',
     'duplicated_text':'.product-page__cart > .product-page__buy-help',
     'duplicated_icons':'.product-page__cart > .features.features--inline.features--circle',
     'consultation':'.megapolis-purchase-consultation a[href="#callback-modal"]',
     'buy_form':'form#s-product-form input[type="submit"]',
    }
    result['elements']={k:page.locator(v).count() for k,v in selectors.items()}
    result['visible']={k:page.locator(v).first.is_visible() if page.locator(v).count() else False for k,v in selectors.items()}
    result['trust_links'] = page.locator('.megapolis-purchase-trust a').count()
    result['horizontal_overflow'] = page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 3')
    if kind=='categorized':
     link=page.locator(selectors['consultation'])
     if link.count():
      link.click(timeout=10000)
      page.wait_for_timeout(400)
      result['modal_visible']=page.locator('#callback-modal').first.is_visible() if page.locator('#callback-modal').count() else False
      result['modal_hash']=page.evaluate('location.hash')[:35]
      result['callback_probe']=page.evaluate("""() => ({
        callbackCount:document.querySelectorAll('#callback-modal').length,
        callbackSnippet:(document.querySelector('#callback-modal')?.outerHTML || '').slice(0,380),
        visibleDialogs:[...document.querySelectorAll('.modal,[role="dialog"],.fancybox-container,.iziModal, .mfp-wrap')]
         .filter(e=>e.getBoundingClientRect().width>0 && getComputedStyle(e).visibility!=='hidden')
         .map(e=>({tag:e.tagName,cls:typeof e.className==='string'?e.className.slice(0,100):'',id:e.id,display:getComputedStyle(e).display})).slice(0,8),
        classnames:[...document.querySelectorAll('a[href="#callback-modal"]')].slice(0,8).map(e=>e.className)
      })""")
      if not result['modal_visible'] and page.locator('.phones__link--callback').count():
       page.locator('.phones__link--callback').first.click(timeout=9000)
       page.wait_for_timeout(550)
       result['header_callback_probe']=page.evaluate("""() => ({
         callbackVisible:!!document.querySelector('#callback-modal') && document.querySelector('#callback-modal').getBoundingClientRect().width>0,
         dialogs:[...document.querySelectorAll('.modal,[role="dialog"],.fancybox-container,.mfp-wrap')].filter(e=>e.getBoundingClientRect().width>0).map(e=>({cls:typeof e.className==='string'?e.className.slice(0,80):'',id:e.id})).slice(0,6)
       })""")
     else:result['modal_visible']=False
    result['page_errors']=errors
    if kind=='categorized':
     result['passed']=bool(
       result['status']==200 and result['elements']['trust']==1 and result['elements']['duplicated_text']==0 and
       (result['elements']['duplicated_icons']==0 or not result['visible']['duplicated_icons']) and
       result['elements']['consultation']==1 and result['elements']['buy_form']>=1 and
       not result['horizontal_overflow'] and not errors
     )
    else:result['passed']=bool(result['status']==200 and not errors)
   except Exception as e:
    result['error']=type(e).__name__+': '+str(e).split('\n')[0][:180]
    result['passed']=False
   tests.append(result)
   print('STAGE43_VISUAL='+json.dumps(result,ensure_ascii=False),flush=True)
   ctx.close()
 browser.close()
print('STAGE43_SUMMARY='+json.dumps({'total':len(tests),'passed':sum(x['passed'] for x in tests)},ensure_ascii=False))
if strict and not all(x['passed'] for x in tests):raise SystemExit(2)
