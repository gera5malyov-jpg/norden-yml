#!/usr/bin/env python3
"""Read-only compare live Avalon extimg URLs against current YML, uniquely by exact name."""
import base64, collections, json, re, urllib.request, xml.etree.ElementTree as ET
from stage52_disable_callback import execute

REMOTE = r"""set -eu
f=$(mktemp /tmp/avalon-catalog-audit-XXXXXX.php)
trap 'rm -f "$f"' EXIT
cat >"$f" <<'PHP'
<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());wa('shop');$m=new waModel();
$rows=$m->query("SELECT id,name,url,summary FROM shop_product WHERE status=1 AND summary LIKE '%avalonmebel.ru/upload/%' LIMIT 2000")->fetchAll();
$items=array();
foreach($rows as $row){
 $links=array();
 preg_match_all('#https?://[A-Za-z0-9/:._?&=%+-]+#i',(string)$row['summary'],$found);
 foreach($found[0] as $value){$links[]=html_entity_decode($value,ENT_QUOTES,'UTF-8');}
 $items[]=array('id'=>$row['id'],'name'=>$row['name'],'url'=>$row['url'],'links'=>$links);
}
echo 'AVALON_CATALOG_B64='.base64_encode(json_encode($items,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)).PHP_EOL;
PHP
chmod 644 "$f"
su -s /bin/bash web -c "php '$f'"
"""

def normalized(value):
    return " ".join((value or "").casefold().split())

def main():
    result = execute(REMOTE)
    encoded = [line.split("=",1)[1] for line in result.splitlines() if line.startswith("AVALON_CATALOG_B64=")]
    if len(encoded)!=1:
        raise RuntimeError("Missing read-only database inventory")
    rows = json.loads(base64.b64decode(encoded[0]))
    req=urllib.request.Request("https://raw.githubusercontent.com/gera5malyov-jpg/norden-yml/main/avalon-podstolya/catalog.yml",headers={"User-Agent":"storefront-readonly-audit"})
    feed=ET.fromstring(urllib.request.urlopen(req,timeout=30).read())
    lookup=collections.defaultdict(list)
    for item in feed.findall(".//offer"):
        name=normalized(item.findtext("name"))
        if name:
            lookup[name].append({"offer_id":item.get("id"),"images":[x.text for x in item.findall("picture") if x.text]})
    report={"site":"profikompany.ru","live_avalon_linked_products":len(rows),"feed_offers":len(feed.findall(".//offer")),
            "exact_unique_name_matches":0,"different_main_image":0,"matching_main_image":0,
            "ambiguous_or_missing":0,"single_link_products":0,"multi_link_products":0,
            "same_gallery_count":0,"different_gallery_count":0,"examples":[]}
    for row in rows:
        candidates=lookup.get(normalized(row["name"]),[])
        if len(candidates)!=1 or not row["links"] or not candidates[0]["images"]:
            report["ambiguous_or_missing"]+=1
            continue
        report["exact_unique_name_matches"]+=1
        if len(row["links"])==1:report["single_link_products"]+=1
        else:report["multi_link_products"]+=1
        if len(row["links"])==len(candidates[0]["images"]):report["same_gallery_count"]+=1
        else:report["different_gallery_count"]+=1
        old=row["links"][0];new=candidates[0]["images"][0]
        if old==new:
            report["matching_main_image"]+=1
        else:
            report["different_main_image"]+=1
            if len(report["examples"])<18:
                report["examples"].append({"id":row["id"],"name":row["name"],"offer_id":candidates[0]["offer_id"],
                 "old":old,"new":new})
    print("AVALON_CATALOG_AUDIT="+json.dumps(report,ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
