#!/usr/bin/env python3
"""Anonymous mobile/desktop visual verification of two contact phone links."""
import json,os
from playwright.sync_api import sync_playwright
BASE='https://profikompany.ru'
os.makedirs('stage47-phone-reports',exist_ok=True)
routes=[('home','/'),('product','/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/'),('checkout','/order/'),('guarantee','/garantiya/')]
result=[]
with sync_playwright() as p:
 browser=p.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
 for mode in ['mobile','desktop']:
  mob=mode=='mobile'
  for name,route in routes:
   ctx=browser.new_context(viewport={'width':390 if mob else 1280,'height':844 if mob else 860},
    is_mobile=mob,has_touch=mob,locale='ru-RU',device_scale_factor=1)
   page=ctx.new_page()
   out={'mode':mode,'page':name,'route':route}
   errors=[];page.on('pageerror',lambda e:errors.append(str(e)[:120]))
   try:
    res=page.goto(BASE+route,wait_until='domcontentloaded',timeout=55000)
    page.wait_for_timeout(950)
    out['status']=res.status if res else None
    out.update(page.evaluate("""() => {
       const phones={spb:'812',msk:'499'};
       let groups={};
       const summary=(nodes)=>[...nodes].map(el=>{
         const r=el.getBoundingClientRect(),style=getComputedStyle(el);
         return {href:el.getAttribute('href'),
           label:(el.textContent||'').trim().slice(0,65),
           visible:r.width>0 && r.height>0 && style.display!=='none' && style.visibility!=='hidden',
           withinWindow:r.left>=-1 && r.right<=innerWidth+1,
           width:Math.round(r.width),
           rectLeft:Math.round(r.left),
           rectTop:Math.round(r.top)}
       });
       groups.top=summary(document.querySelectorAll('.header-top .phones__link--phone'));
       groups.middle=summary(document.querySelectorAll('.header-middle .phones__link--phone'));
       groups.product=summary(document.querySelectorAll('.megapolis-purchase-trust__contact a[href^="tel:"]'));
       groups.footer=summary(document.querySelectorAll('.footer a[href^="tel:"]'));
       groups.all=summary(document.querySelectorAll('a[href^="tel:"]'));
       return {groups,viewportWidth:innerWidth,scrollWidth:document.documentElement.scrollWidth};
     }"""))
    out['pageErrors']=errors
    out['headerBOTH']=(any('812' in (x.get('href') or '') for x in out['groups']['top']+out['groups']['middle']) and
      any('499' in (x.get('href') or '') for x in out['groups']['top']+out['groups']['middle']))
    out['headerBOTHVisible']=(any(x['visible'] and '812' in (x.get('href') or '') for x in out['groups']['top']+out['groups']['middle']) and
      any(x['visible'] and '499' in (x.get('href') or '') for x in out['groups']['top']+out['groups']['middle']))
    out['mobileOverflow']=out['scrollWidth'] > out['viewportWidth']+3
    out['pass']=out['status']==200 and out['headerBOTH'] and not out['mobileOverflow'] and not errors
    if name in ['home','product']:
     page.screenshot(path='stage47-phone-reports/'+mode+'-'+name+'.png',full_page=False)
   except Exception as e:
    out['error']=type(e).__name__+': '+str(e).splitlines()[0][:160];out['pass']=False
   result.append(out)
   print('STAGE47_PHONE='+json.dumps(out,ensure_ascii=False),flush=True)
   ctx.close()
 browser.close()
with open('stage47-phone-reports/summary.json','w',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
print('STAGE47_SUMMARY='+json.dumps({'total':len(result),'pass':sum(x['pass'] for x in result),
'headerBothVisible':sum(x.get('headerBOTHVisible',False) for x in result)}))
if not all(x['pass'] for x in result):raise SystemExit(2)
