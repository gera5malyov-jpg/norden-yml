import json
import math
import os
import sys
import time
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import replace_images_3x4_batch as core

BASE = Path("ozon/all_catalog_3x4")
QUEUE = BASE / "queue.json"
STATE = BASE / "state.json"
REPORTS = BASE / "reports"
BATCH_SIZE = int(os.environ.get("OZON_BATCH_SIZE", "50"))
TEMP_BRANCH = os.environ.get("OZON_TEMP_BRANCH", "").strip()

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def list_products(visibility):
    out=[]
    last_id=""
    for _ in range(1000):
        body={"filter":{"visibility":visibility},"last_id":last_id,"limit":1000}
        d=core.post("/v3/product/list", body)
        res=d.get("result") or {}
        batch=res.get("items") or []
        out.extend(batch)
        new_last=res.get("last_id") or ""
        if not batch or not new_last or new_last==last_id:
            break
        last_id=new_last
        if len(batch)<1000:
            break
    return out

def build_queue():
    all_items=list_products("ALL")
    archived=list_products("ARCHIVED")

    merged={}
    for source, rows in (("ALL",all_items),("ARCHIVED",archived)):
        for x in rows:
            pid=str(x.get("product_id") or x.get("id") or "")
            offer=(x.get("offer_id") or "").strip()
            if not pid or not offer:
                continue
            row=merged.setdefault(pid,{"product_id":int(pid),"offer_id":offer,"sources":[]})
            if source not in row["sources"]:
                row["sources"].append(source)

    offers=[x["offer_id"] for x in merged.values()]
    infos={}
    for i in range(0,len(offers),1000):
        d=core.post("/v3/product/info/list",{"offer_id":offers[i:i+1000]})
        for x in d.get("items") or []:
            pid=str(x.get("id") or x.get("product_id") or "")
            if pid:
                infos[pid]=x

    queue=[]
    for pid,row in merged.items():
        info=infos.get(pid,{})
        created=info.get("created_at") or info.get("created") or info.get("create_date") or info.get("created_time")
        queue.append({
            **row,
            "created_at":created,
            "archived": "ARCHIVED" in row["sources"],
        })
    queue.sort(key=lambda x: (x.get("created_at") or "9999-12-31T23:59:59Z", x["product_id"]))

    data={
        "scope":"ALL + ARCHIVED; no stock filtering",
        "all_list_count":len(all_items),
        "archived_list_count":len(archived),
        "deduplicated_count":len(queue),
        "batch_size":BATCH_SIZE,
        "total_batches":math.ceil(len(queue)/BATCH_SIZE) if queue else 0,
        "items":queue,
    }
    write_json(QUEUE,data)
    write_json(STATE,{
        "status":"RUNNING",
        "next_batch":0,
        "processed_batches":0,
        "processed_cards":0,
        "total_cards":len(queue),
        "total_batches":data["total_batches"],
        "scope":data["scope"],
    })
    print(json.dumps({k:v for k,v in data.items() if k!="items"},ensure_ascii=False,indent=2))

def load_batch(index):
    q=json.loads(QUEUE.read_text(encoding="utf-8"))
    start=index*BATCH_SIZE
    end=min(len(q["items"]),start+BATCH_SIZE)
    return q, q["items"][start:end], start, end

def batch_root(index):
    return BASE / "work" / f"batch_{index:04d}"

def prepare(index):
    q,items,start,end=load_batch(index)
    root=batch_root(index)
    core.ROOT=root
    manifest={
        "batch_index":index,
        "range":[start,end],
        "temp_branch":TEMP_BRANCH,
        "offers":{},
    }
    for pos,row in enumerate(items,start+1):
        offer=row["offer_id"]
        print(f"PREPARE {pos}/{len(q['items'])} {offer}",flush=True)
        try:
            rep=core.prepare_one(offer)
            rep["created_at"]=row.get("created_at")
            rep["archived"]=row.get("archived",False)
            manifest["offers"][offer]=rep
        except Exception as e:
            manifest["offers"][offer]={
                "offer_id":offer,
                "product_id":row.get("product_id"),
                "created_at":row.get("created_at"),
                "archived":row.get("archived",False),
                "prepare_status":"ERROR",
                "error":repr(e),
            }
            print(f"PREPARE ERROR {offer}: {e}",flush=True)
    write_json(root/"manifest.json",manifest)

def apply(index):
    root=batch_root(index)
    core.ROOT=root
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    out={
        "batch_index":index,
        "offers":{},
        "summary":{"requested":len(manifest["offers"]),"success":0,"already_ok":0,"prepare_error":0,"apply_error":0}
    }
    for offer,pre in manifest["offers"].items():
        if pre.get("prepare_status")!="SUCCESS":
            out["offers"][offer]={
                "offer_id":offer,
                "result":"PREPARE_ERROR",
                "error":pre.get("error"),
                "created_at":pre.get("created_at"),
                "archived":pre.get("archived"),
            }
            out["summary"]["prepare_error"]+=1
            continue
        try:
            if int(pre.get("converted_count") or 0)==0:
                result=core.apply_one(pre)
                result["result"]="ALREADY_3X4"
                out["summary"]["already_ok"]+=1
            else:
                result=core.apply_one(pre)
                out["summary"]["success"]+=1
            result["created_at"]=pre.get("created_at")
            result["archived"]=pre.get("archived")
        except Exception as e:
            result={
                "offer_id":offer,
                "product_id":pre.get("product_id"),
                "created_at":pre.get("created_at"),
                "archived":pre.get("archived"),
                "source_image_count":pre.get("source_image_count"),
                "converted_count":pre.get("converted_count"),
                "already_3x4_count":pre.get("already_3x4_count"),
                "result":"APPLY_ERROR",
                "error":repr(e),
            }
            out["summary"]["apply_error"]+=1
            print(f"APPLY ERROR {offer}: {e}",flush=True)
        out["offers"][offer]=result
    write_json(root/"batch_report.json",out)
    print(json.dumps(out["summary"],ensure_ascii=False))

def raw_path_for_branch(url):
    if not TEMP_BRANCH:
        return None
    marker=f"/{TEMP_BRANCH}/"
    if "raw.githubusercontent.com" not in url or marker not in url:
        return None
    return unquote(url.split(marker,1)[1])

def finalize(index):
    root=batch_root(index)
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    batch=json.loads((root/"batch_report.json").read_text(encoding="utf-8"))
    changed=[
        (offer,pre) for offer,pre in manifest["offers"].items()
        if pre.get("prepare_status")=="SUCCESS"
        and int(pre.get("converted_count") or 0)>0
        and batch["offers"].get(offer,{}).get("result")=="SUCCESS"
    ]

    last={}
    keep=set()
    for attempt in range(1,7):
        last={}
        keep=set()
        for offer,pre in changed:
            try:
                item=core.get_item(offer)
                gallery,_=core.ordered_gallery(item)
                raw=[u for u in gallery if raw_path_for_branch(u)]
                for u in raw:
                    keep.add(raw_path_for_branch(u))
                last[offer]={
                    "image_count":len(gallery),
                    "raw_temp_count":len(raw),
                    "materialized":len(raw)==0,
                }
            except Exception as e:
                last[offer]={"materialized":False,"error":repr(e)}
        if all(x.get("materialized") for x in last.values()):
            break
        time.sleep(10)

    keep_file=root/"keep_files.txt"
    keep_file.write_text("\n".join(sorted(keep)) + ("\n" if keep else ""),encoding="utf-8")
    out={
        "batch_index":index,
        "temp_branch":TEMP_BRANCH,
        "changed_products":len(changed),
        "fully_materialized":sum(1 for x in last.values() if x.get("materialized")),
        "still_using_temp_source":sum(1 for x in last.values() if not x.get("materialized")),
        "keep_file_count":len(keep),
        "offers":last,
    }
    write_json(root/"finalize_report.json",out)
    print(json.dumps(out,ensure_ascii=False,indent=2))

def compact(index):
    q,items,start,end=load_batch(index)
    root=batch_root(index)
    batch=json.loads((root/"batch_report.json").read_text(encoding="utf-8"))
    fin=json.loads((root/"finalize_report.json").read_text(encoding="utf-8"))
    compact_offers=[]
    for row in items:
        offer=row["offer_id"]
        r=batch["offers"].get(offer,{})
        compact_offers.append({
            "offer_id":offer,
            "product_id":row["product_id"],
            "created_at":row.get("created_at"),
            "archived":row.get("archived",False),
            "result":r.get("result"),
            "source_image_count":r.get("source_image_count"),
            "converted_count":r.get("converted_count"),
            "already_3x4_count":r.get("already_3x4_count"),
            "final_image_count":r.get("final_image_count"),
            "error":r.get("error"),
        })
    report={
        "batch_index":index,
        "range":[start,end],
        "summary":batch["summary"],
        "finalize":{
            "changed_products":fin.get("changed_products"),
            "fully_materialized":fin.get("fully_materialized"),
            "still_using_temp_source":fin.get("still_using_temp_source"),
            "keep_file_count":fin.get("keep_file_count"),
            "temp_branch":fin.get("temp_branch"),
        },
        "offers":compact_offers,
    }
    write_json(REPORTS/f"batch_{index:04d}.json",report)

    state=json.loads(STATE.read_text(encoding="utf-8"))
    state["processed_batches"]=max(int(state.get("processed_batches") or 0),index+1)
    state["processed_cards"]=max(int(state.get("processed_cards") or 0),end)
    state["next_batch"]=index+1
    state["last_batch"]=index
    state["last_batch_summary"]=batch["summary"]
    if index+1 >= q["total_batches"]:
        state["status"]="COMPLETED"
    write_json(STATE,state)
    print(json.dumps(state,ensure_ascii=False,indent=2))

def status():
    print(STATE.read_text(encoding="utf-8"))

if __name__=="__main__":
    cmd=sys.argv[1] if len(sys.argv)>1 else ""
    if cmd=="build-queue":
        build_queue()
    elif cmd in {"prepare","apply","finalize","compact"}:
        if len(sys.argv)<3:
            raise SystemExit("batch index required")
        idx=int(sys.argv[2])
        globals()[cmd](idx)
    elif cmd=="status":
        status()
    else:
        raise SystemExit("Usage: all_catalog_3x4.py build-queue|prepare N|apply N|finalize N|compact N|status")
