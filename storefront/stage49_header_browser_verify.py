#!/usr/bin/env python3
"""Read-only Chrome visual and interaction audit after mobile header and map fixes."""
import json,os
from playwright.sync_api import sync_playwright
out='storefront-stage49-header'
os.makedirs(out,exist_ok=True)
results=[]
with sync_playwright() as pw:
 browser=pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
 for width in (320,375,390,430,768,1280):
  mobile=width<768
  ctx=browser.new_context(viewport={'width':width,'height':844},is_mobile=mobile,has_touch=mobile,device_scale_factor=1,locale='ru-RU')
  page=ctx.new_page()
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)[:170]))
  row={'width':width}
  try:
   res=page.goto('https://profikompany.ru/',wait_until='domcontentloaded',timeout=65000)
   page.wait_for_timeout(1100)
   row['status']=res.status if res else None
   row['layout']=page.evaluate("""() => {
     const vis = el => {const r=el.getBoundingClientRect();return r.width>0 && r.height>0 && getComputedStyle(el).visibility!=='hidden' && getComputedStyle(el).display!=='none'};
     const nums=[...document.querySelectorAll('.header-top .phones__link--phone')].filter(vis).map(e=>({
       label:e.textContent.trim(),href:e.getAttribute('href'),
       x:Math.round(e.getBoundingClientRect().x),right:Math.round(e.getBoundingClientRect().right),
       y:Math.round(e.getBoundingClientRect().y),bottom:Math.round(e.getBoundingClientRect().bottom)
     }));
     const btn=document.querySelector('.header-middle .categories-button');
     return {
       menuIconVisible:[...document.querySelectorAll('.header-top .mobile-nav-toggle')].filter(vis).length,
       categoryButtonVisible:!!btn&&vis(btn),
       headerCallbackLinks:document.querySelectorAll('.header-top .phones__link--callback').length,
       footerCallbackLinks:document.querySelectorAll('.footer__link[href="#callback-modal"]').length,
       headerHeight:Math.round(document.querySelector('.header-top')?.getBoundingClientRect().height||0),
       viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,
       visiblePhones:nums,
       generalLinks:document.querySelectorAll('.mobile-nav a[href="/dostavka/"],.mobile-nav a[href="/oplata/"]').length
     }
   }""")
   page.screenshot(path=out+'/home-'+str(width)+'.png',full_page=False)
   if mobile:
    button=page.locator('.header-middle .categories-button').first
    button.click(timeout=10000)
    page.wait_for_timeout(500)
    row['catalog_opened_mobile_navigation']=page.locator('.mobile-nav.is-visible').count()>0 and page.locator('.mobile-nav').first.is_visible()
   if width==390:
    page.goto('https://profikompany.ru/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/',wait_until='domcontentloaded',timeout=60000)
    page.wait_for_timeout(400)
    row['product_header_callback_absent']=page.locator('.header-top .phones__link--callback').count()==0
    row['product_cart_button_exists']=page.locator('form#s-product-form .product-add input[type="submit"]').count()>0
   layout=row['layout'];nums=layout['visiblePhones']
   numbers_present=any('812' in x['label'] for x in nums) and any('499' in x['label'] for x in nums)
   overlap=False
   for i,a in enumerate(nums):
    for b in nums[i+1:]:
     if min(a['right'],b['right'])>max(a['x'],b['x'])+1 and min(a['bottom'],b['bottom'])>max(a['y'],b['y'])+1:overlap=True
   row['phone_overlap']=overlap
   row['passed']=(row['status']==200 and layout['headerCallbackLinks']==0 and numbers_present and not overlap and
                  layout['scrollWidth']<=layout['viewport']+3 and layout['categoryButtonVisible'] and
                  (not mobile or (layout['menuIconVisible']==0 and row.get('catalog_opened_mobile_navigation',False))) and
                  (width!=390 or row.get('product_header_callback_absent',False) and row.get('product_cart_button_exists',False)) and
                  not errors)
  except Exception as e:
   row['error']=type(e).__name__+':'+str(e).splitlines()[0][:150]
   row['passed']=False
  row['jsErrorCount']=len(errors)
  results.append(row)
  print('STAGE49_CHECK='+json.dumps(row,ensure_ascii=False),flush=True)
  ctx.close()
 browser.close()
with open(out+'/report.json','w',encoding='utf-8') as f:json.dump(results,f,ensure_ascii=False,indent=2)
print('STAGE49_SUMMARY='+json.dumps({'total':len(results),'passed':sum(r['passed'] for r in results)},ensure_ascii=False),flush=True)
if not all(r['passed'] for r in results):raise SystemExit(3)
