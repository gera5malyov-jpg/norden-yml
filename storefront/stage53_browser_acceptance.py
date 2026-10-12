#!/usr/bin/env python3
"""Anonymous acceptance: layouts, successful cart event and untouched checkout."""
import json,os,sys
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
OUT=Path('storefront-stage53-evidence');OUT.mkdir(exist_ok=True)
BASELINE='--baseline' in sys.argv
ROOT='https://profikompany.ru'
PRODUCT='/stul-dlya-posetiteley-rs42-chernyy-karkas-tkan-chernaya/'
rows=[]

with sync_playwright() as pw:
    browser=pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox'])
    # Same lab conditions and fresh context before/after; no production analytics.
    ctx=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,locale='ru-RU')
    page=ctx.new_page();page.route('**/mc.yandex.*/**',lambda route:route.abort())
    page.add_init_script("window.__sales53Lcp=[];new PerformanceObserver(l=>l.getEntries().forEach(e=>window.__sales53Lcp.push(Math.round(e.startTime)))).observe({type:'largest-contentful-paint',buffered:true});")
    cdp=ctx.new_cdp_session(page);cdp.send('Network.enable');cdp.send('Network.emulateNetworkConditions',{'offline':False,'latency':120,'downloadThroughput':220000,'uploadThroughput':90000,'connectionType':'cellular4g'})
    response=page.goto(ROOT+'/?sales53_perf='+os.getenv('GITHUB_RUN_ID','manual')+('-before' if BASELINE else '-after'),wait_until='domcontentloaded',timeout=75000);page.wait_for_timeout(6000)
    perf=page.evaluate("({lcpMs:Math.max(0,...window.__sales53Lcp),resources:performance.getEntriesByType('resource').filter(r=>r.name.includes('montserrat')||r.name.includes('/img/slider/')).map(r=>({path:new URL(r.name).pathname,bytes:r.transferSize,durationMs:Math.round(r.duration)}))})")
    if not BASELINE:
        before=json.loads((OUT/'baseline-performance.json').read_text())
        old_bytes=sum(r['bytes'] for r in before['resources'] if 'montserrat' in r['path'])
        new_bytes=sum(r['bytes'] for r in perf['resources'] if 'montserrat' in r['path'])
        assert not any('montserrat.min.css' in r['path'] for r in perf['resources']),perf
        assert old_bytes>0 and new_bytes<old_bytes*0.75,(old_bytes,new_bytes)
        perf['comparison']={'beforeLcpMs':before['lcpMs'],'afterLcpMs':perf['lcpMs'],'beforeFontBytes':old_bytes,'afterFontBytes':new_bytes}
    (OUT/('baseline-performance.json' if BASELINE else 'after-performance.json')).write_text(json.dumps(perf,ensure_ascii=False,indent=2))
    page.screenshot(path=str(OUT/('baseline-home-390.png' if BASELINE else 'after-home-390.png')))
    print('STAGE53_PERFORMANCE='+json.dumps({'baseline':BASELINE,**perf}),flush=True);ctx.close()
    if not BASELINE:
        for width in (320,375,390,430,768,1365):
            mobile=width<768
            ctx=browser.new_context(viewport={'width':width,'height':844},is_mobile=mobile,has_touch=mobile,locale='ru-RU')
            page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)[:200]))
            page.route('**/mc.yandex.*/**',lambda route:route.abort())
            page.add_init_script("window.__sales53Goals=[];window.ym=function(id,action,goal){if(action==='reachGoal'){window.__sales53Goals.push(goal);var all=JSON.parse(sessionStorage.getItem('sales53-goals')||'[]');all.push(goal);sessionStorage.setItem('sales53-goals',JSON.stringify(all));}};")
            res=page.goto(ROOT+'/',wait_until='domcontentloaded',timeout=65000);page.wait_for_timeout(650)
            data=page.evaluate("""() => {
              const visible=e=>!!e&&e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0;
              const input=document.getElementById('megapolis-home-search');
              const links=[...document.querySelectorAll('.megapolis-mobile-categories__grid a')];
              return {overflow:document.documentElement.scrollWidth>innerWidth+3,searchVisible:visible(input),searchBottom:input?.getBoundingClientRect().bottom,
                grid:links.map(e=>({x:Math.round(e.getBoundingClientRect().x),y:Math.round(e.getBoundingClientRect().y),width:Math.round(e.getBoundingClientRect().width)})),
                phones:[...document.querySelectorAll('.header-top .phones__link--phone')].filter(visible).map(e=>e.textContent),
                callback:document.querySelectorAll('#callback-modal,.phones__link--callback').length,
                optimizedFont:[...document.querySelectorAll('link[rel=stylesheet]')].some(e=>e.href.includes('montserrat.megapolis53.css'))};
            }""")
            assert res.status==200 and not data['overflow'] and data['callback']==0 and data['optimizedFont'],data
            assert any('812' in p for p in data['phones']) and any('499' in p for p in data['phones']),data
            if mobile:
                assert data['searchVisible'] and data['searchBottom']<400,data
                assert len(data['grid'])==8 and data['grid'][0]['y']==data['grid'][1]['y'] and data['grid'][0]['x']!=data['grid'][1]['x'],data
                page.locator('.megapolis-all-categories summary').click();assert page.locator('.megapolis-all-categories[open]').count()==1
            else:assert not data['searchVisible'],data
            page.screenshot(path=str(OUT/('home-'+str(width)+'.png')))
            rows.append({'width':width,'home':data})
            if width in (390,1365):
                if width==390:
                    page.locator('#megapolis-home-search').fill('РС42')
                    page.wait_for_timeout(120)
                    geometry=page.evaluate("""()=>{const b=document.querySelector('.megapolis-mobile-search button'),r=b.getBoundingClientRect(),h=document.querySelector('header.header:not(.header--fixed)').getBoundingClientRect();return {button:{top:r.top,bottom:r.bottom},header:{top:h.top,bottom:h.bottom},scrollY,hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.tagName}}""")
                    print('STAGE53_SEARCH_GEOMETRY='+json.dumps(geometry),flush=True)
                    page.locator('.megapolis-mobile-search button').click()
                    page.wait_for_url('**/search/**',timeout=25000)
                    assert 'РС42' in page.locator('body').inner_text(),'Model search failed'
                if width==1365:
                    page.goto(ROOT+'/category/kompyuternye-kresla/',wait_until='domcontentloaded',timeout=65000);page.wait_for_timeout(500)
                    label=page.locator('label[for^="megapolis-country-group-"]').filter(has_text='Китай').first
                    assert label.count()==1,'Duplicate China filter was not grouped'
                    if not label.is_visible():page.locator('.category-filter__collapse-arrow').click()
                    with page.expect_response(lambda r:urlparse(r.url).path=='/category/kompyuternye-kresla/' and r.request.resource_type in ('xhr','fetch'),timeout=45000) as filtered:
                        label.click()
                    assert filtered.value.status==200
                    page.wait_for_timeout(700)
                    selected=page.locator('input[name="strana[]"]:checked').count()
                    assert selected>=2,selected
                    page.reload(wait_until='domcontentloaded',timeout=65000);page.wait_for_timeout(500)
                    assert page.locator('input[name="strana[]"]:checked').count()==selected
                    rows[-1]['country_or_filter_preserved_after_reload']=True
                page.goto(ROOT+PRODUCT,wait_until='domcontentloaded',timeout=65000);page.wait_for_timeout(500)
                assert page.locator('.megapolis-key-features .features-list__item').count()<=6
                assert page.locator('#s-product-form').count()==1
                page.screenshot(path=str(OUT/('product-'+str(width)+'.png')))
                button=page.locator('.megapolis-mobile-buy__button') if mobile else page.locator('#s-product-form .product-add input[type=submit]')
                button.first.click(timeout=15000);page.wait_for_url('**/order/**',timeout=30000);page.wait_for_timeout(650)
                assert page.locator('#js-order-page').count()==1
                assert 'РС42' in page.locator('body').inner_text()
                goals=page.evaluate('window.__sales53Goals')
                assert goals.count('cartAdd')==0,'Cart goal belongs to prior product page, current page must not repeat it'
                assert goals.count('checkout_started')==1,goals
                assert 'order_created' not in goals and 'checkout_validation_error' not in goals,goals
                all_goals=page.evaluate("JSON.parse(sessionStorage.getItem('sales53-goals')||'[]')")
                assert all_goals.count('cartAdd')==1,all_goals
                page.screenshot(path=str(OUT/('cart-'+str(width)+'.png')))
                rows[-1]['purchase_to_nonempty_cart']=True;rows[-1]['checkout_goals']=goals;rows[-1]['goals_across_navigation']=all_goals
                # No name/address/payment data and no order creation.
            assert not errors,errors
            ctx.close()
        (OUT/'acceptance.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
        print('STAGE53_ACCEPTANCE='+json.dumps({'widths':len(rows),'purchase_checks':2,'passed':True}),flush=True)
    browser.close()
