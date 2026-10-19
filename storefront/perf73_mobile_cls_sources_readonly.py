#!/usr/bin/env python3
"""Read-only instrumented product CLS and critical resource diagnosis."""
import json,time
from playwright.sync_api import sync_playwright

ORIGIN="https://profikompany.ru"
ROUTES=[
 ("product","/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/"),
 ("product2","/stul-prozrachnyy-pedrali-queen/"),
 ("category","/category/kompyuternye-kresla/"),
]
JS=r"""
(() => {
 window.__audit={shifts:[],lcps:[],tasks:[]};
 const info=(el)=>{if(!el)return null;try{return {
  tag:el.tagName,id:el.id||'',className:typeof el.className==='string'?el.className.slice(0,145):'',
  html:el.outerHTML.slice(0,370),
  rect:el.getBoundingClientRect().toJSON(),
  src:el.getAttribute('src')||'',style:getComputedStyle(el).cssText
 }}catch(e){return {error:String(e)}}};
 try{new PerformanceObserver(list=>list.getEntries().forEach(entry=>{
  window.__audit.shifts.push({t:Math.round(entry.startTime),value:entry.value,hadRecentInput:entry.hadRecentInput,
    sources:(entry.sources||[]).slice(0,8).map(s=>({
     node:info(s.node),from:s.previousRect,to:s.currentRect
    }))});
 })).observe({type:'layout-shift',buffered:true})}catch(e){}
 try{new PerformanceObserver(list=>list.getEntries().forEach(e=>window.__audit.lcps.push({
  t:Math.round(e.startTime),size:e.size,el:info(e.element),url:e.url
 }))).observe({type:'largest-contentful-paint',buffered:true})}catch(e){}
 try{new PerformanceObserver(list=>list.getEntries().forEach(e=>window.__audit.tasks.push({t:Math.round(e.startTime),dur:Math.round(e.duration)}))).observe({type:'longtask',buffered:true})}catch(e){}
})();
"""
def main():
 with sync_playwright() as pw:
  browser=pw.chromium.launch(channel="chrome",headless=True,args=["--no-sandbox"])
  for label,path in ROUTES:
   ctx=browser.new_context(viewport={"width":390,"height":844},device_scale_factor=2,is_mobile=True,has_touch=True,locale="ru-RU")
   page=ctx.new_page()
   cdp=ctx.new_cdp_session(page)
   cdp.send("Network.enable")
   cdp.send("Network.emulateNetworkConditions",{"offline":False,"latency":120,"downloadThroughput":220000,"uploadThroughput":90000,"connectionType":"cellular4g"})
   page.add_init_script(JS)
   try:
    res=page.goto(ORIGIN+path,wait_until="domcontentloaded",timeout=68000)
    page.wait_for_timeout(6500)
    result=page.evaluate("""() => {
      const a=window.__audit,shifts=a.shifts||[];
      const filtered=shifts.filter(s=>!s.hadRecentInput);
      const c=shifts.reduce((sum,s)=>sum+(s.hadRecentInput?0:s.value),0);
      const font=performance.getEntriesByType('resource').filter(s=>/montserrat|fa-solid|fa-brands|fa-regular/.test(s.name)).map(s=>({
        name:s.name.split('/').pop(),duration:Math.round(s.duration),size:s.transferSize
      }));
      return {cls:+c.toFixed(4),shifts:filtered.slice(0,25),lcp:a.lcps.slice(-3),
      longtasks:a.tasks.slice(0,20),fonts:font,
      gallery:document.querySelector('.product-gallery__main')?.outerHTML.slice(0,600)||'',
      marker:!!document.getElementById('megapolis-gallery-cls-20261019'),
      imageCount:document.images.length,
      missingDims:Array.from(document.images).filter(i=>!i.hasAttribute('width')&&!i.hasAttribute('height')).length};
    }""")
    print("PERF73_PAGE="+json.dumps({"page":label,"http":res.status if res else None,"measure":result},ensure_ascii=False),flush=True)
   except Exception as ex:
    print("PERF73_ERROR="+json.dumps({"page":label,"error":str(ex).splitlines()[0][:240]}),flush=True)
   finally:ctx.close()
  browser.close()
if __name__=="__main__":main()
