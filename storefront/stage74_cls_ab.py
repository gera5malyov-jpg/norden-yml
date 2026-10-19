#!/usr/bin/env python3
"""Read-only AB tests for late CLS - no storefront changes."""
import json
from playwright.sync_api import sync_playwright
SITE='https://profikompany.ru/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/'
MODES=('baseline','block_icons','block_fonts','optional_fonts')
JS=r"""(() => {
 window.__probe={shifts:[],last:0};
 try{new PerformanceObserver(list=>{for(const e of list.getEntries()){
 if(e.hadRecentInput)continue;
 const n=document.querySelector('.product-gallery__main');
 const b=document.querySelector('.breadcrumbs');
 window.__probe.shifts.push({t:Math.round(e.startTime),value:Math.round(e.value*100000)/100000,
  gallery:n?{y:Math.round(n.getBoundingClientRect().y),h:Math.round(n.getBoundingClientRect().height)}:null,
  breadcrumb:b?{y:Math.round(b.getBoundingClientRect().y),h:Math.round(b.getBoundingClientRect().height)}:null,
  fonts:document.fonts.status,
  nodes:(e.sources||[]).slice(0,5).map(x=>({className:(typeof x.node?.className==='string'?x.node.className:'').slice(0,70),tag:x.node?.tagName||'',before:x.previousRect?.y,after:x.currentRect?.y}))
 })}}).observe({type:'layout-shift',buffered:true})}catch(e){}
 })();"""
def main():
 with sync_playwright() as p:
  browser=p.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
  for mode in MODES:
   ctx=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,device_scale_factor=2,locale='ru-RU')
   page=ctx.new_page()
   cdp=ctx.new_cdp_session(page)
   cdp.send('Network.enable')
   cdp.send('Network.emulateNetworkConditions',{'offline':False,'latency':120,'downloadThroughput':220000,'uploadThroughput':90000,'connectionType':'cellular4g'})
   page.add_init_script(JS)
   if mode=='block_icons':
    page.route('**/js/fa-*.min.js*',lambda r:r.abort())
    page.route('**/js/fontawesome.min.js*',lambda r:r.abort())
   if mode=='block_fonts':
    page.route('**/css/fonts/*.woff*',lambda r:r.abort())
   if mode=='optional_fonts':
    def replace_css(route):
     res=route.fetch(timeout=24000)
     original=res.body().decode('utf-8','replace')
     route.fulfill(response=res,body=original.replace('font-display:swap','font-display:optional'))
    page.route('**/css/fonts/montserrat.megapolis53.css*',replace_css)
   response=page.goto(SITE,wait_until='domcontentloaded',timeout=70000)
   page.wait_for_timeout(9200)
   result=page.evaluate("""() => ({
    cls:Math.round(window.__probe.shifts.reduce((a,b)=>a+b.value,0)*100000)/100000,
    shifts:window.__probe.shifts,
    fontFacesLoaded:Array.from(document.fonts).filter(f=>f.status==='loaded').map(f=>f.weight).slice(0,22),
    price:!!document.querySelector('.megapolis-mobile-buy__price'),
    cart:!!document.querySelector('.megapolis-mobile-buy__button'),
    iconNodes:document.querySelectorAll('svg.svg-inline--fa').length,
    gallery:document.querySelector('.product-gallery__main')?.getBoundingClientRect().toJSON(),
    nav:performance.getEntriesByType('navigation').map(x=>({ttfb:Math.round(x.responseStart),dom:Math.round(x.domContentLoadedEventEnd)}))
    })""")
   print('STAGE74_TRIAL='+json.dumps({'mode':mode,'http':response.status if response else None,**result},ensure_ascii=False),flush=True)
   ctx.close()
  browser.close()
if __name__=='__main__':main()
