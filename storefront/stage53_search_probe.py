"""Read-only browser preview to isolate header hit-testing, no server writes."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path('stage53-search-probe');out.mkdir(exist_ok=True)
with sync_playwright() as pw:
 b=pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
 for variant in ('outside-header','inside-header','native-header'):
  c=b.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,locale='ru-RU');p=c.new_page()
  p.route('**/mc.yandex.*/**',lambda r:r.abort())
  p.goto('https://profikompany.ru/?search_probe='+variant,wait_until='domcontentloaded',timeout=65000)
  p.add_style_tag(content=Path('storefront/assets/megapolis-mobile-home.css').read_text())
  if variant!='native-header':
   p.evaluate('''()=>{const s=document.createElement('section');s.className='megapolis-mobile-home container';s.innerHTML='<form class="megapolis-mobile-search" method="get" action="/search/"><input id="probe-query" type="search" name="query" placeholder="Название, модель или артикул"><button type="submit">Найти</button></form>';document.querySelector('.slider').before(s)}''')
   if variant=='inside-header':
    p.evaluate('''()=>{const f=document.querySelector('.megapolis-mobile-search');document.querySelector('header.header:not(.header--fixed)').appendChild(f);f.style.margin='10px 15px 12px'}''')
   field=p.locator('#probe-query');button=p.locator('.megapolis-mobile-search button')
  else:
   p.add_style_tag(content='@media(max-width:767px){html body .header .header-middle.header-middle--3 .header-middle__item.header-middle__item--search{display:block!important;order:4;width:100%;flex:0 0 100%;margin-top:12px}.header .header-middle__item--search input{font-size:16px;height:44px}.header .header-middle__item--search button{height:44px}}')
   field=p.locator('.header:not(.header--fixed) .header-middle__item--search input[name=query]').first
   button=p.locator('.header:not(.header--fixed) .header-middle__item--search button').first
  p.evaluate("document.body.classList.add('megapolis-home-page')")
  field.fill('РС42');p.wait_for_timeout(300)
  p.screenshot(path=str(out/(variant+'.png')))
  info=button.evaluate('''e=>{const r=e.getBoundingClientRect(),h=document.querySelector('header.header:not(.header--fixed)'),s=getComputedStyle(h),before=getComputedStyle(h,'::before'),after=getComputedStyle(h,'::after');return {button:{x:r.x,y:r.y,width:r.width,height:r.height},headerRect:JSON.parse(JSON.stringify(h.getBoundingClientRect())),headerClass:h.className,header:{position:s.position,zIndex:s.zIndex},before:{content:before.content,position:before.position,top:before.top,bottom:before.bottom,width:before.width,height:before.height,pointerEvents:before.pointerEvents,zIndex:before.zIndex},after:{content:after.content,position:after.position,height:after.height,zIndex:after.zIndex},hits:document.elementsFromPoint(r.x+r.width/2,r.y+r.height/2).slice(0,6).map(n=>({tag:n.tagName,class:n.className,id:n.id}))}}''')
  info['action']=field.evaluate('e=>e.form.action')
  info['documents']=[]
  p.on('response',lambda r:info['documents'].append({'url':r.url,'status':r.status}) if r.request.resource_type=='document' else None)
  info['click_completed']=False
  try:
   button.click(timeout=5000);info['click_completed']=True
   p.wait_for_timeout(5000)
  except Exception as e:info['error']=str(e)[:1200]
  info['url_after_click']=p.url
  info['result_titles']=p.locator('.product-item__title').all_text_contents()[:10]
  info['heading']=p.locator('h1').all_text_contents()[:3]
  info['query_value']=field.input_value() if field.count() else None
  p.screenshot(path=str(out/(variant+'-after.png')))
  print('SEARCH_PROBE='+json.dumps({'variant':variant,**info},ensure_ascii=False),flush=True)
  c.close()
 b.close()
