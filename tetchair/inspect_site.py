import json
import os
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BASE="https://price.tetchair.ru"
s=requests.Session()
s.headers.update({"User-Agent":"Megapolis-Tetchair-Sync/1.0"})
r=s.post(BASE+"/login/",data={"login":os.environ["TETCHAIR_LOGIN"],"pass":os.environ["TETCHAIR_PASSWORD"]},timeout=40,allow_redirects=True)
r.raise_for_status()
if BeautifulSoup(r.text,"html.parser").select_one("form.login_form"):
    raise SystemExit("login failed")

rr=s.get(BASE+"/catalog/3009/",timeout=60)
rr.raise_for_status()
soup=BeautifulSoup(rr.text,"html.parser")
pg=[]
for a in soup.find_all("a",href=True):
    txt=" ".join(a.get_text(" ",strip=True).split())
    href=a.get("href") or ""
    if txt in ("Предыдущая","Следующая") or txt.isdigit() or "page" in href.lower() or "start" in href.lower():
        pg.append({"text":txt,"href":href,"class":a.get("class")})
cards=[]
for a in soup.select('a[href*="?p="]')[:30]:
    txt=" ".join(a.get_text(" ",strip=True).split())
    cards.append({"href":urljoin(BASE,a.get("href")),"text":txt[:500]})
print(json.dumps({"pagination":pg,"cards":cards},ensure_ascii=False,indent=2))
