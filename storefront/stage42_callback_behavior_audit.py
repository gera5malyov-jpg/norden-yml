"""Anonymous browser-only check of existing and new storefront callback triggers."""
import json
from playwright.sync_api import sync_playwright
base='https://profikompany.ru/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/'
with sync_playwright() as p:
 browser=p.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
 for mode,width in (('desktop',1365),('mobile',390)):
  for name,selector in (('new_product_link','.megapolis-purchase-consultation a'),('existing_header_callback','a.phones__link--callback')):
   ctx=browser.new_context(viewport={'width':width,'height':844},is_mobile=(mode=='mobile'),has_touch=(mode=='mobile'),locale='ru-RU')
   page=ctx.new_page()
   errors=[]
   page.on('pageerror',lambda e:errors.append(type(e).__name__))
   res=page.goto(base,wait_until='domcontentloaded',timeout=65000)
   page.wait_for_timeout(3500)
   result={'mode':mode,'test':name,'http':res.status if res else None,'link_count':page.locator(selector).count()}
   def state():
    return page.evaluate("""() => {
      const names=['#callback-modal','.mfp-wrap','.mfp-bg','.fancybox-container','.fancybox-overlay','.modal','.modal-backdrop','[role="dialog"]'];
      const arr=[];
      for(const q of names){
        for(const e of [...document.querySelectorAll(q)].slice(0,5)){
          const b=e.getBoundingClientRect(),s=getComputedStyle(e);
          arr.push({q,tag:e.tagName,classes:(typeof e.className==='string'?e.className:'').slice(0,160),
             visible:s.display!=='none'&&s.visibility!=='hidden'&&b.width>0&&b.height>0,
             height:Math.round(b.height),opacity:s.opacity});
        }
      }
      return {hash:location.hash,forms:[...document.querySelectorAll('form')].filter(e=>{
          const z=e.getBoundingClientRect(),s=getComputedStyle(e);
          return z.width>0&&z.height>0&&s.display!=='none'&&s.visibility!=='hidden'
        }).map(e=>({cls:(typeof e.className==='string'?e.className:'').slice(0,100),
           action:(e.getAttribute('action')||'').split('?')[0].slice(0,80)})).slice(0,12),
        nodes:arr,
        activeTag:document.activeElement?.tagName||'',
        bodyClass:document.body.className
      };
    }""")
   result['before']=state()
   try:
    page.locator(selector).first.click(timeout=14000)
    page.wait_for_timeout(1600)
    result['after']=state()
   except Exception as exc:
    result['error']=type(exc).__name__+': '+str(exc).splitlines()[0][:200]
   result['js_errors']=errors
   print('STAGE42_CALLBACK='+json.dumps(result,ensure_ascii=False),flush=True)
   ctx.close()
 browser.close()
