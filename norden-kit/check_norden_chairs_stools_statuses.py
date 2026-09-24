#!/usr/bin/env python3
import importlib.util, json, os
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parent
AUDIT=ROOT/"norden_chairs_stools_duplicate_audit.json"
OUT=ROOT/"norden_chairs_stools_status_check.json"

spec=importlib.util.spec_from_file_location("norden_sync", ROOT/"sync_norden_kit.py")
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
kit=mod.KitClient(os.environ.get("YANDEX_KIT_TOKEN",""))
audit=json.loads(AUDIT.read_text(encoding="utf-8"))

groups=[]
tot=Counter()
for g in audit.get("groups") or []:
    rows=[g.get("kept") or {}]+list(g.get("duplicates") or [])
    live=[]
    for r in rows:
        vid=mod.s(r.get("variant_id"))
        if not vid: continue
        try:
            x=kit.get_variant(vid)
            status=mod.s(x.get("status")).upper()
            sku=mod.s(x.get("sku"))
            kit_id=x.get("kit_id")
        except Exception as e:
            status="READ_ERROR"; sku=mod.s(r.get("sku")); kit_id=r.get("kit_id")
        tot[status]+=1
        live.append({"variant_id":vid,"sku":sku,"kit_id":kit_id,"status":status})
    published=sum(1 for x in live if x["status"]=="PUBLISHED")
    groups.append({
        "article":g.get("article"),
        "published_count":published,
        "all_nonpublished": published==0,
        "items":live
    })

report={
    "groups_total":len(groups),
    "status_counts":dict(tot),
    "groups_with_zero_published":sum(1 for g in groups if g["all_nonpublished"]),
    "groups_with_multiple_published":sum(1 for g in groups if g["published_count"]>1),
    "zero_published_groups":[g for g in groups if g["all_nonpublished"]],
    "groups":groups
}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({
    "groups_total":report["groups_total"],
    "status_counts":report["status_counts"],
    "groups_with_zero_published":report["groups_with_zero_published"],
    "groups_with_multiple_published":report["groups_with_multiple_published"]
},ensure_ascii=False,indent=2))
