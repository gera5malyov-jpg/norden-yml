#!/usr/bin/env python3
"""Read-only homepage image-LCP and broken asset investigation."""
import json,os
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
OUT='stage29-home-audit'
os.makedirs(OUT,exist_ok=True)
def public_url(u):
  p=urlsplit(u)
  return (p.netloc if p.netloc!='profikompany.ru' else 'site')+(p.path[:220] if '/my/' not in p.path else '[private]')
with sync_playwright() as p:
  browser=p.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
  results=[]
  for mode in ['desktop','mobile']:
    mobile=mode=='mobile'
    ctx=browser.new_context(viewport={'width':390 if mobile else 1365,'height':844 if mobile else 900},is_mobile=mobile,has_touch=mobile,device_scale_factor=1,locale='ru-RU')
    page=ctx.new_page()
    lcp=[]
    page.add_init_script("""(() => {
      window.__lcpDetails=[];
      try{
       new PerformanceObserver(list=>{
        for(const e of list.getEntries()){
          const el=e.element;
          window.__lcpDetails.push({
            start:Math.round(e.startTime),
            tag:el?.tagName || '',
            className:typeof el?.className === 'string' ? el.className.slice(0,120):'',
            src:el?.currentSrc || el?.getAttribute?.('data-src') || '',
            outer:(el?.outerHTML||'').slice(0,480),
            ancestorClass:typeof el?.parentElement?.className === 'string' ? el.parentElement.className.slice(0,160):'',
            renderSize:e.size||0,
            rect:el?.getBoundingClientRect?{
              width:Math.round(el.getBoundingClientRect().width),
              height:Math.round(el.getBoundingClientRect().height),
              top:Math.round(el.getBoundingClientRect().top)
            }:null
          });
        }
       }).observe({type:'largest-contentful-paint',buffered:true});
      }catch(e){}
    })()""")
    if mobile:
      cdp=ctx.new_cdp_session(page);cdp.send('Network.enable')
      cdp.send('Network.emulateNetworkConditions',{'offline':False,'latency':120,'downloadThroughput':220000,'uploadThroughput':90000,'connectionType':'cellular4g'})
    bad=[]
    page.on('response',lambda rsp: bad.append({'status':rsp.status,'url':public_url(rsp.url),'type':rsp.request.resource_type}) if rsp.status>=400 else None)
    rsp=page.goto('https://profikompany.ru/',wait_until='domcontentloaded',timeout=70000)
    page.wait_for_timeout(4500 if not mobile else 6000)
    data=page.evaluate("""() => {
      const imgs=[...document.images];
      const res=performance.getEntriesByType('resource').map(r=>({
        name:r.name,initiator:r.initiatorType,transfer:r.transferSize||0,
        duration:Math.round(r.duration),start:Math.round(r.startTime)
      }));
      const top=res.sort((a,b)=>b.transfer-a.transfer).slice(0,15);
      const candidates=imgs.filter(e=>{
        const rect=e.getBoundingClientRect();
        return rect.top < window.innerHeight && rect.bottom > 0;
      }).slice(0,50).map(e=>({
        className:e.className,src:e.currentSrc||e.getAttribute('data-src')||'',
        loading:e.loading,complete:e.complete,
        naturalWidth:e.naturalWidth,naturalHeight:e.naturalHeight,
        renderedWidth:Math.round(e.getBoundingClientRect().width),
        renderedHeight:Math.round(e.getBoundingClientRect().height),
        attrsWidth:e.getAttribute('width'),attrsHeight:e.getAttribute('height')
      }));
      return {lcp:window.__lcpDetails || [],top,candidates,
        documentWidth:document.documentElement.scrollWidth,viewportWidth:innerWidth};
    }""")
    for item in data['lcp']:
      item['src']=public_url(item.get('src','')) if item.get('src') else ''
    for item in data['top']:
      item['name']=public_url(item['name'])
    for item in data['candidates']:
      item['src']=public_url(item['src']) if item['src'] else ''
    result={'mode':mode,'http':rsp.status,'badResponses':bad[:30],
      'lcpEvents':data['lcp'][-8:],
      'topTransfers':data['top'],
      'visibleImages':data['candidates'][:25],
      'layout':{k:data[k] for k in ['documentWidth','viewportWidth']}}
    page.screenshot(path=f'{OUT}/{mode}-home.png',full_page=False)
    results.append(result)
    print('STAGE29_HOME='+json.dumps(result,ensure_ascii=False),flush=True)
    ctx.close()
  browser.close()
with open(OUT+'/results.json','w',encoding='utf-8') as f:json.dump(results,f,ensure_ascii=False,indent=2)
