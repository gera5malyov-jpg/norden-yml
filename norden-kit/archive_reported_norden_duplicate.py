#!/usr/bin/env python3
import importlib.util, json, os
from pathlib import Path

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("norden_sync", ROOT/"sync_norden_kit.py")
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
kit=mod.KitClient(os.environ.get("YANDEX_KIT_TOKEN",""))

KEEP="AF-30756476"
ARCHIVE="100-1594419"

chars=kit.characteristics()
matches=[x for x in chars if mod.norm_title(x.get("title"))==mod.norm_title(mod.CODE_SITE_TITLE)]
if len(matches)!=1:
    raise SystemExit(f"Cannot resolve {mod.CODE_SITE_TITLE}: {len(matches)} matches")
code_id=mod.s(matches[0].get("id"))

def exact(sku):
    payload=kit.request("GET","/v1/variants",params={"name":sku,"page":1,"per_page":100})
    rows=[x for x in kit.items(payload) if mod.s(x.get("sku"))==sku]
    if len(rows)!=1:
        raise SystemExit(f"{sku}: expected exactly one active/searchable variant, got {len(rows)}")
    return rows[0]

keep=exact(KEEP)
dup=exact(ARCHIVE)

keep_code=mod.current_char_value(keep,code_id)
dup_code=mod.current_char_value(dup,code_id)
checks={
    "keep_brand_norden": mod.s(keep.get("brand")).casefold()=="norden",
    "dup_brand_norden": mod.s(dup.get("brand")).casefold()=="norden",
    "same_nonempty_code_for_site": bool(keep_code) and mod.norm_code(keep_code)==mod.norm_code(dup_code),
    "keep_not_archived": mod.s(keep.get("status")).upper()!="ARCHIVED",
    "dup_not_archived": mod.s(dup.get("status")).upper()!="ARCHIVED",
}
if not all(checks.values()):
    print(json.dumps({"checks":checks,"keep":keep,"duplicate":dup},ensure_ascii=False,indent=2))
    raise SystemExit("Safety stop: duplicate pair did not pass all checks")

# Zero duplicate stocks first, then archive. Keep original untouched.
warehouses=[x for x in kit.warehouses() if mod.s(x.get("id")) and mod.s(x.get("status")).upper()!="ARCHIVED"]
kit.bulk_stocks([
    {"variant_id":mod.s(dup.get("id")),"warehouse_id":mod.s(w.get("id")),"quantity":0}
    for w in warehouses
])
archive_error = ""
try:
    kit.patch_variant(mod.s(dup.get("id")),{"status":"ARCHIVED"})
except Exception as exc:
    archive_error = str(exc)
    # KIT API may prohibit direct archive transitions for some variants.
    # In that case hide the verified duplicate from storefront instead.
    kit.patch_variant(mod.s(dup.get("id")),{"status":"HIDDEN"})

verified=kit.get_variant(mod.s(dup.get("id")))
result={
    "result":"archived_duplicate",
    "kept":{
        "sku":KEEP,
        "variant_id":mod.s(keep.get("id")),
        "kit_id":keep.get("kit_id"),
        "code_for_site":keep_code,
    },
    "archived":{
        "sku":ARCHIVE,
        "variant_id":mod.s(dup.get("id")),
        "kit_id":dup.get("kit_id"),
        "code_for_site":dup_code,
        "status_after":mod.s(verified.get("status")),
        "archive_error":archive_error,
    },
    "checks":checks,
}
print(json.dumps(result,ensure_ascii=False,indent=2))
if mod.s(verified.get("status")).upper() not in ("ARCHIVED","HIDDEN"):
    raise SystemExit("Duplicate could not be archived or hidden")
