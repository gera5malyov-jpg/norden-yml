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
    const suspects=[...document.querySelectorAll('body *')]
      .filter(el => {
        const r=el.getBoundingClientRect(),st=getComputedStyle(el);
        return r.width>0 && r.height>0 &&
          st.display!=='none' && st.visibility!=='hidden' &&
          st.position!=='fixed' && r.right>vw+5 && r.left>=-3;
      })
      .map(el=>({tag:el.tagName,cls:typeof el.className==='string'?el.className.slice(0,140):'',
        id:el.id, text:(el.textContent||'').trim().slice(0,70),
        x:Math.round(el.getBoundingClientRect().x),
        right:Math.round(el.getBoundingClientRect().right),
        width:Math.round(el.getBoundingClientRect().width),
        parent:el.parentElement?.className?.toString?.().slice(0,80) || ''}))
      .sort((a,b)=>b.right-a.right);
    return {viewport:vw,scrollWidth:document.documentElement.scrollWidth,suspects:suspects.slice(0,40)};
  }""")
  p.screenshot(path=folder+'/'+str(width)+'.png',full_page=False)
  print('STAGE50_TABLET='+json.dumps({'width':width,'result':result},ensure_ascii=False),flush=True)
  ctx.close()
 b.close()
