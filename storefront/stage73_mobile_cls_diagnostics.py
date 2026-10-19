#!/usr/bin/env python3
"""Read-only mobile layout-shift audit, collect actual contributing elements."""
import json
from playwright.sync_api import sync_playwright
ORIGIN='https://profikompany.ru'
ROUTES=[('product','/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/'),('product2','/stul-prozrachnyy-pedrali-queen/'),('category','/category/kompyuternye-kresla/')]
JS=r"""(() => {
window.__cls_audit={shifts:[],lcp:[]};
function identify(n){if(!n)return null;return {
 tag:n.tagName||'',id:(n.id||'').slice(0,80),
 classes:(typeof n.className==='string'?n.className:'').slice(0,170),
 outer:(n.outerHTML||'').slice(0,220)}}
try{new PerformanceObserver(list=>{for(const e of list.getEntries()){
 if(e.hadRecentInput)continue;
 window.__cls_audit.shifts.push({
 t:Math.round(e.startTime),value:Math.round(e.value*100000)/100000,
 sources:(e.sources||[]).slice(0,8).map(x=>({node:identify(x.node),
 prev:x.previousRect?{y:Math.round(x.previousRect.y),h:Math.round(x.previousRect.height),x:Math.round(x.previousRect.x),w:Math.round(x.previousRect.width)}:null,
 next:x.currentRect?{y:Math.round(x.currentRect.y),h:Math.round(x.currentRect.height),x:Math.round(x.currentRect.x),w:Math.round(x.currentRect.width)}:null}))
 })}}).observe({type:'layout-shift',buffered:true})}catch(e){}
try{new PerformanceObserver(l=>l.getEntries().forEach(e=>window.__cls_audit.lcp.push({ms:Math.round(e.startTime),element:identify(e.element)}))).observe({type:'largest-contentful-paint',buffered:true})}catch(e){}
})();"""
def main():
 with sync_playwright() as p:
  browser=p.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
  for label,route in ROUTES:
   ctx=browser.new_context(viewport={'width':390,'height':844},device_scale_factor=2,is_mobile=True,has_touch=True,locale='ru-RU')
   page=ctx.new_page()
   client=ctx.new_cdp_session(page)
   client.send('Network.enable')
   client.send('Network.emulateNetworkConditions',{'offline':False,'latency':120,'downloadThroughput':220000,'uploadThroughput':90000,'connectionType':'cellular4g'})
   page.add_init_script(JS)
   response=page.goto(ORIGIN+route,wait_until='domcontentloaded',timeout=65000)
   page.wait_for_timeout(9000)
   data=page.evaluate("""() => ({
    shifts:window.__cls_audit?.shifts||[], lcp:window.__cls_audit?.lcp.slice(-4)||[],
    gallery:document.querySelector('.product-gallery__main')?{
     rect:document.querySelector('.product-gallery__main').getBoundingClientRect().toJSON(),
     ratio:getComputedStyle(document.querySelector('.product-gallery__main')).aspectRatio
    }:null,
    images:document.images.length,
    headStyle:!!document.querySelector('#megapolis-gallery-cls-20261019'),
    fixedBuy:document.querySelector('#megapolis-mobile-buy')?.getBoundingClientRect().toJSON(),
    webFonts:[...document.fonts].filter(x=>x.status==='loaded').length,
    dimensions:{innerWidth,scrollWidth:document.documentElement.scrollWidth}
    })""")
   summary={'label':label,'http':response.status if response else None,'cls':round(sum(x['value'] for x in data['shifts']),5),
            'shifts':data['shifts'][:20],'lcp':data['lcp'],'gallery':data['gallery'],'headStyle':data['headStyle'],
            'fixedBuy':data['fixedBuy'],'images':data['images'],'webFonts':data['webFonts'],'dimensions':data['dimensions']}
   print('STAGE73_LAYOUT='+json.dumps(summary,ensure_ascii=False),flush=True)
   ctx.close()
  browser.close()
if __name__=='__main__':main()
