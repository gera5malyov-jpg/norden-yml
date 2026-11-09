#!/usr/bin/env python3
"""Remove only below-floor Ozon SKUs from the specific MIN_EXT_PRICE strategy.

No price, min_price, old_price, stock or promotion-price writes in this step.
"""
import json,time
from collections import defaultdict
from pathlib import Path
from promo_v2_reconcile import api,load_prices,active_prices_and_bad,chunks,num

STRATEGY_ID="3abf9219-eb93-4cd1-b483-05c2290e3614"
OUT=Path("ozon/promo_strategy_floor_remediation_20261109.json")
report={"status":"STARTED","before_below":None,"after_below":None,
        "selected_for_detach":0,"strategy_deleted":0,"strategy_still_linked":[],
        "not_in_expected_strategy":[],"removal_errors":[],"prices_still_below_minimum":[],
        "min_price_lowered":[],"action_details":[]}
try:
    strategies=api("/v1/pricing-strategy/list",{"page":1,"limit":49}).get("strategies") or []
    match=next((s for s in strategies if str(s.get("id"))==STRATEGY_ID),None)
    if not match or match.get("type")!="MIN_EXT_PRICE" or not match.get("enabled"):
        raise RuntimeError("Expected active MIN_EXT_PRICE strategy missing; refusing mutations")
    before=load_prices()
    actions=api("/v1/actions",method="GET").get("result") or []
    participants, _, violations=active_prices_and_bad(actions,before)
    report["before_below"]=len(violations)
    action_counts=defaultdict(int)
    for aid, pid, floor, _ in violations: action_counts[aid]+=1
    report["action_details"]=[{"action_id":aid,"count":cnt} for aid,cnt in action_counts.items()]
    targets=sorted({int(pid) for _,pid,_,_ in violations})
    if len(targets)>350:
        raise RuntimeError(f"Unexpected {len(targets)} violations > safe cap 350")
    print("PRECHECK",json.dumps({"violations":len(violations),"unique_products":len(targets),"by_action":dict(action_counts)},ensure_ascii=False),flush=True)
    membership={}
    for batch in chunks(targets,50):
        d=api("/v1/pricing-strategy/strategy-ids-by-product-ids",{"product_id":[str(i) for i in batch]})
        for row in (d.get("result") or {}).get("products_info") or []:
            membership[int(row["product_id"])]=str(row.get("strategy_id") or "")
    selected=[pid for pid in targets if membership.get(pid)==STRATEGY_ID]
    report["not_in_expected_strategy"]=[pid for pid in targets if membership.get(pid)!=STRATEGY_ID]
    report["selected_for_detach"]=len(selected)
    print("MEMBERSHIP",len(selected),"of",len(targets),"linked to expected strategy",flush=True)
    for batch in chunks(selected,50):
        d=api("/v1/pricing-strategy/products/delete",{"product_id":[str(i) for i in batch]})
        result=d.get("result") or {}
        failed=int(result.get("failed_product_count") or 0)
        if failed:
            report["removal_errors"].append({"requested":len(batch),"failed":failed,"details":result})
            print("DELETE_BATCH_ERROR",json.dumps(result,ensure_ascii=False)[:2100],flush=True)
        else:
            report["strategy_deleted"]+=len(batch)
            print("DELETE_BATCH",len(batch),"successful",flush=True)
    time.sleep(12)
    still={}
    for batch in chunks(selected,50):
        d=api("/v1/pricing-strategy/strategy-ids-by-product-ids",{"product_id":[str(i) for i in batch]})
        for row in (d.get("result") or {}).get("products_info") or []:
            still[int(row["product_id"])]=str(row.get("strategy_id") or "")
    report["strategy_still_linked"]=[pid for pid in selected if still.get(pid)==STRATEGY_ID]
    after=load_prices()
    for pid in selected:
        p=after.get(pid)
        old=before.get(pid)
        if not p or not old:continue
        original=num((old.get("price") or {}).get("min_price"))
        current_min=num((p.get("price") or {}).get("min_price"))
        current_price=num((p.get("price") or {}).get("price"))
        if current_min<original:
            report["min_price_lowered"].append({"product_id":pid,"before":str(original),"after":str(current_min)})
        if current_min>0 and current_price<current_min:
            report["prices_still_below_minimum"].append({"product_id":pid,"min_price":str(current_min),"price":str(current_price),"offer_id":p.get("offer_id")})
    final_participants,_,final_bad=active_prices_and_bad(actions,after)
    report["after_below"]=len(final_bad)
    report["remaining_by_action"]={str(aid):sum(1 for x in final_bad if x[0]==aid) for aid in action_counts}
    report["remaining_sample"]=[{"action_id":aid,"product_id":pid,"min_price":str(floor),"action_price":str(num(row.get("action_price")))} for aid,pid,floor,row in final_bad[:40]]
    report["status"]="COMPLETE"
    print("FINAL_STRATEGY_REPORT",json.dumps({k:v for k,v in report.items() if k not in ("remaining_sample","prices_still_below_minimum")},ensure_ascii=False),flush=True)
    print("STILL_BELOW_SAMPLE",json.dumps(report["remaining_sample"][:15],ensure_ascii=False),flush=True)
    if report["strategy_still_linked"] or report["min_price_lowered"] or report["removal_errors"]:
        raise RuntimeError("Incomplete strategy detach or seller minimum unexpectedly changed")
finally:
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
