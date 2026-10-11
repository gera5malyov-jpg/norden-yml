#!/usr/bin/env python3
"""Anonymous, non-ordering browser probe of product buy buttons and cart transition.

A test item can be added to a temporary session cart; no checkout submission.
"""
import json
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
ROOT='https://profikompany.ru'
PATH='/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/'
with sync_playwright() as pw:
 browser=pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
 for kind in ('mobile-sticky','desktop-product'):
  mobile=kind.startswith('mobile')
  ctx=browser.new_context(viewport={'width':390 if mobile else 1365,'height':844 if mobile else 900},
                          is_mobile=mobile,has_touch=mobile,device_scale_factor=1,locale='ru-RU')
  page=ctx.new_page()
  events=[]
  def sent(req):
   if '/cart/' in req.url or '/order/' in req.url:
    events.append({'event':'request','path':urlparse(req.url).path,'method':req.method})
  def recv(resp):
   if '/cart/' in resp.url or '/order/' in resp.url:
    rowdata={'event':'response','path':urlparse(resp.url).path,'status':resp.status}
    if '/cart/add/' in resp.url:
     try:
      obj=resp.json()
      if isinstance(obj,dict):
       rowdata['response_keys']=list(obj.keys())[:15]
       rowdata['status_field']=obj.get('status')
       rowdata['errors_present']=bool(obj.get('errors'))
       rowdata['data_type']=type(obj.get('data')).__name__
      else:rowdata['response_type']=type(obj).__name__
     except Exception as e:rowdata['response_parse_error']=type(e).__name__
    events.append(rowdata)
  page.on('request',sent);page.on('response',recv)
  row={'mode':kind}
  try:
   doc=page.goto(ROOT+PATH,wait_until='domcontentloaded',timeout=65000)
   page.wait_for_timeout(900)
   sku=page.locator('form#s-product-form input[name="sku_id"]').input_value()
   row['sku_id']=sku
   row['quantity']=page.locator('form#s-product-form input[name="quantity"]').input_value()
   target=page.locator('.megapolis-mobile-buy__button') if mobile else page.locator('form#s-product-form .product-add input[type="submit"]')
   row['button_exists']=bool(target.count())
   row['before_url']=page.url
   target.first.click(timeout=18000)
   page.wait_for_timeout(4000)
   row['after_click_url']=page.url
   row['buy_button_value']=page.locator('form#s-product-form .product-add input[type="submit"]').first.get_attribute('value')
   row['added_panel_visible']=page.locator('form#s-product-form .product-added').first.is_visible()
   if page.url.rstrip('/')==ROOT+PATH.rstrip('/'):
    response=page.goto(ROOT+'/order/',wait_until='domcontentloaded',timeout=60000)
    page.wait_for_timeout(800)
    row['cart_http']=response.status if response else None
   row['after_order_url']=page.url
   body=page.locator('body').inner_text()
   row['cart_markers']={s:s in body for s in ('РС42','SAMS-533054','Стул для посетителей','Оформление заказа','Корзина','1 936')}
   row['events']=events[:20]
   row['test_ok']=row['cart_markers'].get('РС42',False) and row['cart_http']==200 if 'cart_http' in row else row['cart_markers'].get('РС42',False)
  except Exception as e:
   row['error']=type(e).__name__+': '+str(e).splitlines()[0][:200]
  print('STAGE47_PROBE='+json.dumps(row,ensure_ascii=False),flush=True)
  ctx.close()
 browser.close()
