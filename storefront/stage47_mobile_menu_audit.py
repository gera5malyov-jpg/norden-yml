#!/usr/bin/env python3
"""Read-only click probe for both mobile navigation controls."""
import json
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
 browser=pw.chromium.launch(channel="chrome", headless=True, args=["--no-sandbox"])
 for width in (390,375,430,1280):
  ctx=browser.new_context(viewport={"width":width,"height":850}, is_mobile=width<768, has_touch=width<768, device_scale_factor=1)
  page=ctx.new_page()
  errors=[]
  page.on("pageerror",lambda e:errors.append(str(e)[:150]))
  page.goto('https://profikompany.ru/',wait_until='domcontentloaded',timeout=60000)
  page.wait_for_timeout(1500)
  def snap(label):
   x=page.evaluate("""() => {
     const get = sel => [...document.querySelectorAll(sel)].slice(0,4).map(e=>({
       text:e.textContent.trim().slice(0,70),
       c:typeof e.className==='string'?e.className.slice(0,120):'',
       visible:!!(e.getBoundingClientRect().width&&e.getBoundingClientRect().height&&getComputedStyle(e).visibility!=='hidden'),
       aria:e.getAttribute('aria-expanded')||'',x:Math.round(e.getBoundingClientRect().x),width:Math.round(e.getBoundingClientRect().width)
     }));
     return {topHamb:get('.header-top .mobile-nav-toggle'),brownButton:get('.header-middle .categories-button'),
       mobileNav:get('.mobile-nav'),catNav:get('.categories-nav'),catList:get('.categories-nav__list'),
       bodyClass:document.body.className,htmlClass:document.documentElement.className,
       overflow:document.documentElement.scrollWidth-innerWidth};
   }""")
   print('STAGE47_MENU='+json.dumps({'width':width,'step':label,'state':x,'jsErrors':errors},ensure_ascii=False),flush=True)
  snap('initial')
  if width<768:
   top=page.locator('.header-top .mobile-nav-toggle').first
   if top.count():
    top.click(timeout=7000)
    page.wait_for_timeout(650)
    snap('top-click')
    try:top.click(timeout=4000)
    except Exception:pass
    page.wait_for_timeout(400)
   button=page.locator('.header-middle .categories-button').first
   if button.count():
    try:button.click(timeout=7000)
    except Exception as e:print('STAGE47_CLICK_ERROR='+json.dumps({'width':width,'error':str(e).splitlines()[0][:130]}),flush=True)
    page.wait_for_timeout(650)
    snap('brown-click')
  ctx.close()
 browser.close()
