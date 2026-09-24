import json,re,unicodedata,requests,xml.etree.ElementTree as ET
from pathlib import Path
def n(x): return re.sub(r"[^0-9a-zа-яё]+","",unicodedata.normalize("NFKC",str(x or "").strip()).casefold())
def f(x):
    try:return float(str(x or "0").replace("\xa0","").replace(" ","").replace(",","."))
    except:return 0.0
ids={n(x) for x in Path("catalog/current_catalog_yml_ids.txt").read_text(encoding="utf-8").splitlines() if x.strip()}
url="https://norden.group/index.php?dispatch=sw_user_prices.get_file&file=Norden.group+-K8%25.xml"
r=requests.get(url,timeout=180,headers={"User-Agent":"Mozilla/5.0"}); r.raise_for_status()
root=ET.fromstring(r.content)
out=[]
for x in root.iter("Номенклатура"):
    a=(x.findtext("Артикул") or "").strip()
    name=(x.findtext("НаименованиеПолное") or x.findtext("Наименование") or "").strip()
    if not a or "кресл" not in name.casefold() or n(a) in ids: continue
    stock=sum(f(z.text) for z in x.findall("СвободныйОстаток"))
    if stock<=0: continue
    p={str(z.attrib.get("ВидЦен") or "").strip():f(z.text) for z in x.findall("Цена")}
    out.append({"yml_id":a,"name":name,"stock":stock,"opt":p.get("Опт",0),"rrp":p.get("РРЦ",0)})
out.sort(key=lambda z:(-z["stock"],z["name"]))
Path("catalog/norden_missing_instock_chairs.json").write_text(json.dumps({"count":len(out),"items":out},ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"count":len(out),"items":out},ensure_ascii=False))