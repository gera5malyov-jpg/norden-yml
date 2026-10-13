#!/usr/bin/env python3
"""Anonymous Chrome QA for phase 2; never submits a checkout or creates an order."""
import json,os,re
from pathlib import Path
BASE=Path(__file__).parent
ROOT='https://profikompany.ru'
OUT=Path('storefront-stage55-evidence')

def assert_exact_metadata(actual,expected,field,url):
 if actual!=expected:
  raise AssertionError('Category metadata mismatch: '+json.dumps({'field':field,'url':url,'expected':expected,'actual':actual},ensure_ascii=False))

def main():
 from playwright.sync_api import sync_playwright
 OUT.mkdir(exist_ok=True);proposal=json.loads((BASE/'stage55-proposal.json').read_text());checks=[]
 rollout=json.loads((OUT/'rollout.json').read_text())['database']
 proposal['products']=[p for p in proposal['products'] if p['id'] in rollout['applied_product_ids']]
 proposal['categories']=[p for p in proposal['categories'] if p['id'] in rollout['applied_category_ids']]
 if not proposal['products']:raise RuntimeError('No eligible descriptions applied; inspect guarded skips')
 with sync_playwright() as pw:
  browser=pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
  for width in (390,1365):
   ctx=browser.new_context(viewport={'width':width,'height':844},is_mobile=width<768,has_touch=width<768,locale='ru-RU');page=ctx.new_page();errors=[]
   page.on('pageerror',lambda e:errors.append(str(e)[:160]))
   page.route('**/mc.yandex.*/**',lambda route:route.abort())
   page.add_init_script("window.__content55Goals=[];window.ym=function(id,action,goal){if(action==='reachGoal')window.__content55Goals.push(goal);};")
   for category in proposal['categories']:
    res=page.goto(ROOT+category['url'],wait_until='domcontentloaded',timeout=65000)
    assert res.status==200,'Category HTTP failure'
    fields=category['set']
    actual_title=page.title()
    actual_description=page.locator('meta[name=description]').get_attribute('content')
    observed={'url':page.url,'actual_title':actual_title,'expected':fields,'actual_description':actual_description}
    response_head=res.text().split('</head>',1)[0]
    raw_title=re.search(r'<title[^>]*>(.*?)</title>',response_head,re.S|re.I)
    observed['response_title_html']=raw_title.group(1) if raw_title else None
    (OUT/('category-meta-'+str(category['id'])+'-'+str(width)+'.json')).write_text(json.dumps(observed,ensure_ascii=False,indent=2))
    print('STAGE55_CATEGORY_META='+json.dumps(observed,ensure_ascii=False),flush=True)
    if 'meta_title' in fields:assert_exact_metadata(actual_title,fields['meta_title'],'title',page.url)
    if 'meta_description' in fields:assert_exact_metadata(actual_description,fields['meta_description'],'description',page.url)
    assert page.locator('h1').count()==1,'Category heading count changed'
   res=page.goto(ROOT+'/dostavka/',wait_until='domcontentloaded',timeout=65000)
   assert res.status==200
   assert page.locator('#megapolis-delivery-calculator-note').count()==1
   assert page.locator('script[src*="transportnye-kompanii.com"]').count()==0,'Blocking external calculator remains'
   assert 'Менеджер уточнит город отправления' in page.locator('#megapolis-delivery-calculator-note').inner_text()
   assert 'от 600' not in page.locator('body').inner_text()
   page.screenshot(path=str(OUT/('delivery-'+str(width)+'.png')),full_page=True)
   for product in proposal['products'][:6]:
    res=page.goto(ROOT+product['url'],wait_until='domcontentloaded',timeout=65000)
    assert res.status==200,'Product HTTP failure'
    expected=re.findall(r'<li>(.*?)</li>',product['set']['description'])
    content=page.content()
    # Content may sit behind an untouched native description tab.
    assert all(spec in content for spec in expected),'Product factual description absent'
    assert page.locator('#s-product-form').count()==1,'Native product form changed'
   res=page.goto(ROOT+'/',wait_until='domcontentloaded',timeout=65000)
   phones=page.locator('a[href^="tel:"]').evaluate_all('(xs)=>xs.map(x=>x.getAttribute("href")).join(" ")')
   assert '2449264' in phones and '6776332' in phones,'Paired phones changed'
   assert page.locator('#callback-modal,.phones__link--callback').count()==0
   assert page.locator('script[src*="megapolis-funnel-v2.js"]').count()==1
   assert page.locator('script[src*="megapolis-delivery-v1.js"]').count()==1
   assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+3'),'Horizontal overflow'
   # A known positive-stock product. Only the cart is populated in a new session.
   product=proposal['products'][0]
   page.goto(ROOT+product['url'],wait_until='domcontentloaded',timeout=65000);page.wait_for_timeout(400)
   trust=page.locator('.megapolis-purchase-trust').inner_text()
   assert 'разных городов' in trust and 'согласуется отдельно' in trust
   button=page.locator('.megapolis-mobile-buy__button') if width<768 else page.locator('#s-product-form .product-add input[type=submit]')
   button.first.click(timeout=15000);page.wait_for_url('**/order/**',wait_until='domcontentloaded',timeout=45000);page.wait_for_timeout(700)
   assert page.locator('#js-order-page').count()==1
   assert page.locator('#js-order-form').count()==1,'Native checkout form absent'
   assert page.locator('#megapolis-delivery-notice').count()==1
   notice=page.locator('#megapolis-delivery-notice').inner_text()
   assert 'Стоимость товаров не является окончательной стоимостью заказа с доставкой' in notice
   assert product['expected']['name'][:20] in page.locator('body').inner_text(),'Anonymous cart product absent'
   goals=page.evaluate('window.__content55Goals')
   assert 'order_created' not in goals,'An order-created signal appeared without submission'
   assert 'checkout_started' in goals,'Nonempty checkout-start signal absent'
   labels=page.locator('#js-order-form').inner_text()
   assert 'Доставка от 890 руб' not in labels and 'Самовывоз от 450 руб' not in labels
   assert not errors,errors
   page.screenshot(path=str(OUT/('nonempty-cart-'+str(width)+'.png')),full_page=True)
   checks.append({'width':width,'categories_checked':len(proposal['categories']),'products_checked':min(6,len(proposal['products'])),'delivery_note':True,'nonempty_native_checkout':True,'order_submitted':False,'order_created_signal':False,'shipping_choices_rendered':bool(page.locator('#js-order-form .wa-type-wrapper').count())})
   ctx.close()
  browser.close()
 (OUT/'acceptance.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
 print('STAGE55_ACCEPTANCE='+json.dumps({'success':True,'widths':len(checks),'orders_created':0}),flush=True)

if __name__=='__main__':main()
