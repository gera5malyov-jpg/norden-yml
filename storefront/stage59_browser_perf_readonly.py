#!/usr/bin/env python3
"""Read-only targeted laboratory audit. No cart/order interactions, no writes to the website."""
import json,time,statistics,os
from collections import Counter
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
ORIGIN='https://profikompany.ru'
ROUTES=[('home','/'),('category','/category/kompyuternye-kresla/'),('product','/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/'),('info','/oplata/')]
def target(url):
 p=urlsplit(url)
 if p.hostname=='profikompany.ru':return p.path[:75]
 return p.hostname or '?'
def main():
 rows=[]
 with sync_playwright() as pw:
  browser=pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
  for mobile in (True,False):
   for label,path in ROUTES:
    ctx=browser.new_context(viewport={'width':390,'height':844} if mobile else {'width':1365,'height':900},device_scale_factor=2 if mobile else 1,is_mobile=mobile,has_touch=mobile,locale='ru-RU')
    page=ctx.new_page()
    cdp=ctx.new_cdp_session(page)
    if mobile:
     cdp.send('Network.enable')
     cdp.send('Network.emulateNetworkConditions',{'offline':False,'latency':120,'downloadThroughput':220000,'uploadThroughput':90000,'connectionType':'cellular4g'})
    page.add_init_script("""(() => {
      window.__perf={lcp:0,lcpElement:'',cls:0,shifts:[],longTasks:[],jsErrors:[]};
      try{new PerformanceObserver(l=>l.getEntries().forEach(e=>{window.__perf.lcp=Math.round(e.startTime);window.__perf.lcpElement=(e.element?.tagName||'').toLowerCase()})).observe({type:'largest-contentful-paint',buffered:true})}catch(e){}
      try{new PerformanceObserver(l=>l.getEntries().forEach(e=>{if(!e.hadRecentInput){window.__perf.cls+=e.value;window.__perf.shifts.push({value:e.value,sources:(e.sources||[]).slice(0,5).map(x=>({tag:x.node?.tagName,className:(typeof x.node?.className==='string'?x.node.className:'').slice(0,150),id:(x.node?.id||'').slice(0,80),previousRect:x.previousRect,currentRect:x.currentRect}))});}})).observe({type:'layout-shift',buffered:true})}catch(e){}
      try{new PerformanceObserver(l=>l.getEntries().forEach(e=>{window.__perf.longTasks.push(Math.round(e.duration))})).observe({type:'longtask',buffered:true})}catch(e){}
      window.addEventListener('error',e=>window.__perf.jsErrors.push((e.message||'js').slice(0,90)));
    })()""")
    failures=[]
    page.on('requestfailed',lambda req:failures.append({'location':target(req.url),'resource':req.resource_type,'failure':str(req.failure)[:60]}))
    jsErrors=[]
    page.on('pageerror',lambda e:jsErrors.append(str(e)[:200]))
    start=time.monotonic()
    try:
     resp=page.goto(ORIGIN+path,wait_until='domcontentloaded',timeout=65000)
     page.wait_for_timeout(4800 if mobile else 3200)
     result=page.evaluate("""() => {
      const nav=performance.getEntriesByType('navigation')[0]||{};
      const resources=performance.getEntriesByType('resource');
      const p=window.__perf||{};
      const slow=resources.filter(x=>x.duration>250).sort((a,b)=>b.duration-a.duration).slice(0,12).map(x=>({host:(new URL(x.name)).host,duration:Math.round(x.duration),type:x.initiatorType,path:(new URL(x.name)).pathname.slice(0,90)}));
      return {ttfb:Math.round(nav.responseStart||0),dom:Math.round(nav.domContentLoadedEventEnd||0),load:Math.round(nav.loadEventEnd||0),fcp:Math.round((performance.getEntriesByType('paint').find(x=>x.name==='first-contentful-paint')||{}).startTime||0),lcp:p.lcp||0,lcpElement:p.lcpElement||'',cls:Math.round((p.cls||0)*10000)/10000,longTasks:p.longTasks?.slice(0,22)||[],errors:p.jsErrors?.slice(0,8)||[],shifts:p.shifts?.slice(0,20)||[],scripts:document.scripts.length,images:document.images.length,unboundedImages:[...document.images].filter(x=>!x.hasAttribute('height')&&!x.hasAttribute('width')).length,resourceCount:resources.length,slowAssets:slow,viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,ymType:typeof window.ym,ecommerceEvents:Array.isArray(window.dataLayer)?window.dataLayer.filter(x=>x&&x.ecommerce).length:null,loadedEcommerce:!!window.__megapolisEcomV1};
    }""")
     status=resp.status if resp else None
    except Exception as e:
     result={'error':type(e).__name__+': '+str(e).splitlines()[0][:140]};status=None
    row={'device':'mobile-throttled' if mobile else 'desktop','page':label,'http':status,'elapsed':round(time.monotonic()-start,1),'metrics':result,'requestFailures':failures[:12],'pageErrors':jsErrors[:12]}
    rows.append(row)
    print('STAGE59_PAGE='+json.dumps(row,ensure_ascii=False),flush=True)
    ctx.close()
  browser.close()
 print('STAGE59_SUMMARY='+json.dumps({'page_count':len(rows),'errors':sum(1 for x in rows if x['http']!=200),'mobileLCP':{x['page']:x['metrics'].get('lcp') for x in rows if x['device']=='mobile-throttled'},'cls':{x['page']:x['metrics'].get('cls') for x in rows if x['device']=='mobile-throttled'}},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
