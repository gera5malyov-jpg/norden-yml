import json,os,re,unicodedata,requests,xml.etree.ElementTree as ET,gspread
from google.oauth2.service_account import Credentials
def n(x): return re.sub(r"[^0-9a-zа-яё]+","",unicodedata.normalize("NFKC",str(x or "").strip()).casefold())
def f(x):
    try:return float(str(x or "0").replace("\xa0","").replace(" ","").replace(",","."))
    except:return 0
gc=gspread.authorize(Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]))
ws=gc.open_by_key("1DvMYvKRr8fX8mfLFaeJCXCccQofPViPcIP2CgIctc8w").worksheet("Норден")
v=ws.get_all_values(); h=v[0]; yi=h.index("YML ID"); ai=h.index("Артикул")
ids={n(r[yi]) for r in v[1:] if len(r)>yi and r[yi]}; arts={n(r[ai]) for r in v[1:] if len(r)>ai and r[ai]}
url="https://norden.group/index.php?dispatch=sw_user_prices.get_file&file=Norden.group+-K8%25.xml"
root=ET.fromstring(requests.get(url,timeout=180,headers={"User-Agent":"Mozilla/5.0"}).content)
out=[]
for x in root.iter("Номенклатура"):
    a=(x.findtext("Артикул") or "").strip(); name=(x.findtext("НаименованиеПолное") or x.findtext("Наименование") or "").strip()
    if not a or "кресл" not in name.casefold(): continue
    stock=sum(f(z.text) for z in x.findall("СвободныйОстаток"))
    if stock<=0 or n(a) in ids or n(a) in arts: continue
    p={str(z.attrib.get("ВидЦен") or "").strip():f(z.text) for z in x.findall("Цена")}
    out.append({"yml_id":a,"name":name,"stock":stock,"opt":p.get("Опт",0),"rrp":p.get("РРЦ",0)})
out.sort(key=lambda z:(-z["stock"],z["name"]))
open("catalog/norden_missing_instock_chairs.json","w").write(json.dumps({"count":len(out),"items":out},ensure_ascii=False,indent=2))
print(json.dumps({"count":len(out),"top":out[:20]},ensure_ascii=False))