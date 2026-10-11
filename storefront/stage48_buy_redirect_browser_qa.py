#!/usr/bin/env python3
"""Pre-deployment and post-deployment buy-button test in anonymous browser sessions.

Test clicks either sticky (mobile) or native (desktop) Buy, asserts that a
successful /cart/add/ precedes navigation to /order/ containing the item.
No order/checkout confirmation is submitted.
"""
import json,os,pathlib
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

ROOT='https://profikompany.ru'
PATH='/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/'
PREVIEW=os.getenv('INJECT_BUY_SCRIPT','1')=='1'
js=pathlib.Path(__file__).parent.joinpath('assets/megapolis-buy-to-cart-v1.js').read_text(encoding='utf-8')
all_rows=[]

with sync_playwright() as pw:
 browser=pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
 for mode in ('mobile','desktop'):
  mobile=mode=='mobile'
  ctx=browser.new_context(viewport={'width':390 if mobile else 1365,'height':844 if mobile else 900},
   device_scale_factor=1,is_mobile=mobile,has_touch=mobile,locale='ru-RU')
  page=ctx.new_page()
  page_errors=[]
  add_results=[]
  page.on('pageerror',lambda exc:page_errors.append(str(exc)[:150]))
  def on_response(resp):
   if urlparse(resp.url).path=='/cart/add/':
    status_field=''
    try:
     status_field=resp.json().get('status')
    except Exception:pass
    add_results.append({'http':resp.status,'status':status_field})
  page.on('response',on_response)
  row={'mode':mode,'injected':PREVIEW}
  try:
   init=page.goto(ROOT+PATH,wait_until='domcontentloaded',timeout=60000)
   row['product_http']=init.status if init else None
   page.wait_for_timeout(750)
   if PREVIEW:page.add_script_tag(content=js)
   else:
    row['loaded_asset']=page.evaluate("""()=>
      [...document.scripts].some(e=>e.src.includes('megapolis-buy-to-cart-v1.js'))""")
   button=page.locator('.megapolis-mobile-buy__button') if mobile else page.locator('#s-product-form .product-add input[type="submit"]')
   button.first.click(timeout=15000)
   page.wait_for_url('**/order/**',timeout=22000,wait_until='domcontentloaded')
   page.wait_for_timeout(900)
   row['destination']=urlparse(page.url).path
   row['cart_add_results']=add_results
   cart_text=page.locator('body').inner_text()
   row['correct_product_in_cart']=('РС42' in cart_text and 'Стул для посетителей' in cart_text)
   row['horizontal_overflow']=page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 3')
   row['javascript_errors']=page_errors
   row['pass']=bool(row['product_http']==200 and row['destination']=='/order/' and
      len(add_results)==1 and add_results[0]['http']==200 and add_results[0]['status']=='ok' and
      row['correct_product_in_cart'] and not page_errors and not row['horizontal_overflow'])
  except Exception as exc:
   row['pass']=False
   row['error']=type(exc).__name__+': '+str(exc).splitlines()[0][:175]
   row['url_when_failed']=urlparse(page.url).path
   row['cart_add_results']=add_results
  finally:
   ctx.close()
  all_rows.append(row)
  print('STAGE48_BROWSER='+json.dumps(row,ensure_ascii=False),flush=True)
 browser.close()

if not all(row['pass'] for row in all_rows):
 raise SystemExit('Buy and redirect QA failed')
