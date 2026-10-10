#!/usr/bin/env python3
"""Read-only Playwright network-failure and storefront performance audit.

Only an anonymous test cart is mutated in an isolated browser session.
No order submission, payments, account access, catalog edits, or DB writes.
"""
import json
import os
import statistics
import time
from collections import Counter
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, Error as PWError

ORIGIN = "https://profikompany.ru"
REPORTDIR = "storefront-stage28-reports"
os.makedirs(REPORTDIR, exist_ok=True)


def public_location(url):
    """Drop queries, fragments, auth tokens, and user information."""
    x = urlsplit(url)
    host = x.hostname or ""
    path = x.path
    if host == "profikompany.ru":
        # Public storefront routes; never print secret or personal paths.
        if any(z in path.lower() for z in ("/webasyst", "/my/", "/account/", "/login/", "/checkout/", "/order/")):
            path = "[private-path]"
        else:
            path = path[:110]
        return "same-origin:" + path
    return "external-host:" + host[:90]


def install_metrics(page):
    page.add_init_script("""() => {
      window.__auditMetrics = {cls:0, lcp:0, lcpElement:null, errors:[], longTasks:[]};
      try {
        new PerformanceObserver(list => {
          for (const e of list.getEntries()) {
            if (!e.hadRecentInput) window.__auditMetrics.cls += e.value;
          }
        }).observe({type:'layout-shift', buffered:true});
      } catch(e) {}
      try {
        new PerformanceObserver(list => {
          for (const e of list.getEntries()) {
            window.__auditMetrics.lcp=e.startTime;
            window.__auditMetrics.lcpElement=(e.element?.tagName || '').toLowerCase();
          }
        }).observe({type:'largest-contentful-paint', buffered:true});
      } catch(e) {}
      try {
        new PerformanceObserver(list => {
          for (const e of list.getEntries()) {
            window.__auditMetrics.longTasks.push(Math.round(e.duration));
          }
        }).observe({type:'longtask', buffered:true});
      } catch(e) {}
      window.addEventListener('error',e => window.__auditMetrics.errors.push('js'),true);
    }""")


def audit_checkout(browser):
    records = []
    for mode in ("desktop", "mobile"):
        mobile = mode == "mobile"
        context = browser.new_context(
            viewport={"width":390 if mobile else 1365,"height":844 if mobile else 900},
            device_scale_factor=2 if mobile else 1,
            is_mobile=mobile, has_touch=mobile, locale="ru-RU",
        )
        page = context.new_page()
        requests_failed = []
        http_errors = []
        js_errors = []
        milestones = []
        stage = ["home"]

        def fail(req):
            reason = str(req.failure or "")
            # collect only public sanitized locations; no POST payload, query strings or cookies
            requests_failed.append({
                "stage":stage[0], "method":req.method,
                "resource":req.resource_type,
                "url":public_location(req.url),
                "reason":reason[:180],
            })

        def response(resp):
            if resp.status >= 400:
                http_errors.append({
                    "stage":stage[0], "status":resp.status,
                    "url":public_location(resp.url),
                })

        page.on("requestfailed", fail)
        page.on("response", response)
        page.on("pageerror", lambda e:js_errors.append(type(e).__name__))
        result = {"mode":mode,"failed_requests":requests_failed, "http_errors":http_errors,
                  "js_error_types":js_errors,"milestones":milestones}
        try:
            page.goto(ORIGIN + "/",wait_until="domcontentloaded",timeout=50000)
            page.locator("form.add-to-cart").first.locator('input[type="submit"]').click(timeout=12000)
            page.wait_for_timeout(1600)
            stage[0] = "cart"
            checkout = page.goto(ORIGIN + "/order/", wait_until="domcontentloaded",timeout=50000)
            milestones.append({"name":"checkout", "http":checkout.status if checkout else None})
            page.wait_for_timeout(1000)
            if page.locator('select[name="region[region]"]').count():
                vals = page.locator('select[name="region[region]"] option').evaluate_all(
                    "(xs)=>xs.map(e=>({v:e.value,t:(e.textContent||'').trim()}))")
                spb = next((x for x in vals if "Санкт-Петербург" in x["t"] and x["v"]),None)
                if spb:
                    page.locator('select[name="region[region]"]').select_option(value=spb["v"])
                    page.locator('input[name="region[city]"]').fill("Санкт-Петербург")
                    page.locator('input[name="region[city]"]').press("Tab")
                    page.wait_for_timeout(1200)
                    stage[0] = "shipping-list"
                    button=page.get_by_role('button',name='Выбрать доставку')
                    if button.count() and button.first.is_visible():
                        button.first.click(timeout=12000)
                        page.wait_for_timeout(2200)
                        milestones.append({"name":"shipping-list","visible":("Доставка от 890 руб" in page.locator("body").inner_text())})
                        stage[0]="shipping-choice"
                        choice=page.get_by_text("Доставка от 890 руб",exact=False)
                        if choice.count():
                            choice.first.click(timeout=12000)
                            page.wait_for_timeout(3000)
                            milestones.append({
                                "name":"payment-list",
                                "visible":("Оплата СБП QR-кодом" in page.locator("body").inner_text())
                            })
            result["success"]=any(x.get("name")=="payment-list" and x.get("visible") for x in milestones)
        except Exception as exc:
            result["success"]=False
            result["test_error"]=type(exc).__name__ + ": " + str(exc).splitlines()[0][:160]
        finally:
            context.close()
        counts=Counter((x["url"],x["reason"],x["stage"]) for x in requests_failed)
        result["failure_groups"]=[{"location":u,"reason":r,"stage":st,"count":n}
            for (u,r,st),n in counts.most_common(25)]
        records.append(result)
        print("STAGE28_NETWORK=" + json.dumps(result,ensure_ascii=False,default=str),flush=True)
    return records


def audit_speed(browser):
    measurements = []
    # A small fixed representative set: home, category, live product, cart.
    routes = [
        ("home","/"),
        ("category","/category/kompyuternye-kresla/"),
        ("product","/stul-polubarnyy-barni-700-chernyy-latte-b05/"),
        ("cart","/order/"),
    ]
    for mode in ("desktop","mobile"):
        mobile=(mode=="mobile")
        for label,route in routes:
            ctx=browser.new_context(
                viewport={"width":390 if mobile else 1365,"height":844 if mobile else 900},
                device_scale_factor=2 if mobile else 1,is_mobile=mobile,has_touch=mobile,
                locale="ru-RU",cache_enabled=False
            ) if False else browser.new_context(
                viewport={"width":390 if mobile else 1365,"height":844 if mobile else 900},
                device_scale_factor=2 if mobile else 1,is_mobile=mobile,has_touch=mobile,
                locale="ru-RU"
            )
            page=ctx.new_page()
            install_metrics(page)
            # Simulated mobile network for indicative lab data. Do not claim field CWV.
            if mobile:
                cdp=ctx.new_cdp_session(page)
                cdp.send("Network.enable")
                cdp.send("Network.emulateNetworkConditions",{
                    "offline":False,"latency":120,"downloadThroughput":220000,
                    "uploadThroughput":90000,"connectionType":"cellular4g"
                })
            failed=Counter()
            status_by_type=Counter()
            page.on("requestfailed",lambda req:failed.update([req.resource_type]))
            page.on("response",lambda resp:status_by_type.update([str(resp.status)]) if resp.status>=400 else None)
            start=time.monotonic()
            status=None
            err=None
            try:
                response=page.goto(ORIGIN+route,wait_until="domcontentloaded",timeout=65000)
                status=response.status if response else None
                page.wait_for_timeout(3500 if not mobile else 5000)
                perf=page.evaluate("""() => {
                  const n=performance.getEntriesByType('navigation')[0] || {};
                  const paint=performance.getEntriesByType('paint') || [];
                  const res=performance.getEntriesByType('resource') || [];
                  const typeCount={}; const typeBytes={};
                  for(const r of res){const k=r.initiatorType || 'other';typeCount[k]=(typeCount[k]||0)+1;typeBytes[k]=(typeBytes[k]||0)+(r.transferSize||0);}
                  const fcp=paint.find(e=>e.name==='first-contentful-paint');
                  return {
                    nav:{ttfb:n.responseStart||null, domInteractive:n.domInteractive||null,
                         domContentLoaded:n.domContentLoadedEventEnd||null,
                         load:n.loadEventEnd||null},
                    fcp:fcp?.startTime || null,
                    lcp:window.__auditMetrics?.lcp || null,
                    lcpElement:window.__auditMetrics?.lcpElement || null,
                    cls:window.__auditMetrics?.cls ?? null,
                    longTasks:(window.__auditMetrics?.longTasks||[]).slice(0,50),
                    resources:res.length,types:typeCount,transferBytes:typeBytes,
                    documentScrollWidth:document.documentElement.scrollWidth,
                    viewportWidth:window.innerWidth,
                    imageElements:document.images.length,
                    imagesWithoutDimensions:[...document.images].filter(x=>!x.hasAttribute('width') && !x.hasAttribute('height')).length,
                    lazyImageElements:[...document.images].filter(x=>x.loading==='lazy'||x.classList.contains('isLazyLoad')).length
                  };
                }""")
            except Exception as e:
                err=type(e).__name__+":"+str(e).splitlines()[0][:140]
                perf={}
            elapsed=round((time.monotonic()-start)*1000)
            row={"mode":mode,"page":label,"route":route,"http":status,"lab_elapsed_ms":elapsed,
                 "browser_failures":dict(failed),"http_errors":dict(status_by_type),
                 "timings":perf,"error":err}
            print("STAGE28_SPEED="+json.dumps(row,ensure_ascii=False,default=str),flush=True)
            measurements.append(row)
            ctx.close()
    return measurements


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch(channel="chrome",headless=True,args=["--no-sandbox"])
        result={}
        result["checkout"]=audit_checkout(browser)
        result["performance"]=audit_speed(browser)
        browser.close()
    with open(os.path.join(REPORTDIR,"stage28-report.json"),"w",encoding="utf-8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print("STAGE28_SUMMARY="+json.dumps({"network":len(result["checkout"]),
         "speed":len(result["performance"]), "report":REPORTDIR+"/stage28-report.json"},ensure_ascii=False))
if __name__=="__main__":
    main()
