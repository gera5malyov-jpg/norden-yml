#!/usr/bin/env python3
"""Read-only Chrome audit to locate x-overflow at 768px tablet width."""
import json,os
from playwright.sync_api import sync_playwright
folder='storefront-stage50-tablet'
os.makedirs(folder,exist_ok=True)
with sync_playwright() as pw:
 b=pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
 for width in (767,768,800,991):
  ctx=b.new_context(viewport={'width':width,'height':850},is_mobile=width<768,has_touch=width<768,device_scale_factor=1)
  p=ctx.new_page()
  p.goto('https://profikompany.ru/',wait_until='domcontentloaded',timeout=65000)
  p.wait_for_timeout(800)
  result=p.evaluate("""() => {
    const vw=innerWidth;
    const clipped=el=>{
      let p=el.parentElement;
      while(p && p!==document.body){
        const cs=getComputedStyle(p),r=p.getBoundingClientRect();
        if(['hidden','clip','scroll','auto'].includes(cs.overflowX) &&
          r.right <= vw+4 && r.left>=-4 && r.width>0) return true;
        p=p.parentElement;
      }
      return false;
    };
    const suspects=[...document.querySelectorAll('body *')]
      .filter(el=>{
        const r=el.getBoundingClientRect(),st=getComputedStyle(el);
        return r.width>0 && r.height>0 && st.display!=='none' &&
          st.visibility!=='hidden' && st.position!=='fixed' &&
          r.right>vw+5 && r.left>=-3 && !clipped(el);
      }).map(el=>({tag:el.tagName,
        cls:typeof el.className==='string'?el.className.slice(0,110):'',
        id:el.id, text:(el.textContent||'').trim().slice(0,48),
        x:Math.round(el.getBoundingClientRect().x),
        right:Math.round(el.getBoundingClientRect().right),
        width:Math.round(el.getBoundingClientRect().width),
        parent:el.parentElement?.className?.toString?.().slice(0,70)||''
      })).sort((a,b)=>a.right-b.right);
    const sections=['body','.header-top','.header-top__row','.header-middle','.header-middle__row','.main','.main__wrapper','.slider','.owl-stage-outer','.catalog-nav','.mobile-nav','.welcome-note','.footer'];
    const blocks=sections.map(sel=>{
      const e=document.querySelector(sel);if(!e)return null;
      const x=e.getBoundingClientRect(),st=getComputedStyle(e);
      return {sel,w:Math.round(x.width),right:Math.round(x.right),
        scroll:e.scrollWidth,client:e.clientWidth,overflowX:st.overflowX};
    }).filter(Boolean);
    return {viewport:vw,scrollWidth:document.documentElement.scrollWidth,
      suspects:suspects.slice(0,45),blocks};
  }""")
  p.screenshot(path=folder+'/'+str(width)+'.png',full_page=False)
  print('STAGE50_TABLET='+json.dumps({'width':width,'result':result},ensure_ascii=False),flush=True)
  ctx.close()
 b.close()
