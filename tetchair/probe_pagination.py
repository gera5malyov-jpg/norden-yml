import json, re, runpy
from bs4 import BeautifulSoup

ns=runpy.run_path("tetchair/inspect_site.py", init_globals={"re":re})
s=ns["s"]
BASE=ns["BASE"]
out={}
for cid in ("2942","2943","3833","4001","4081","2682"):
    r=s.get(f"{BASE}/catalog/{cid}/",timeout=60)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    cards=soup.select('a[href*="?p="]')
    snippets=[]
    for txt in ("Следующая","Предыдущая"):
        node=soup.find(string=lambda x: x and txt in x)
        if node:
            p=node.parent
            for _ in range(4):
                if p and len(str(p))<12000:
                    snippets.append(str(p))
                p=p.parent if p else None
    out[cid]={
        "cards":len(cards),
        "codes":[re.search(r"Код:\s*(\d+)", " ".join(a.get_text(" ",strip=True).split())).group(1)
                 for a in cards if re.search(r"Код:\s*(\d+)", " ".join(a.get_text(" ",strip=True).split()))][:30],
        "pager_html":snippets[:6],
        "buttons":[{"text":" ".join(x.get_text(" ",strip=True).split()),"attrs":x.attrs}
                   for x in soup.find_all(["button","input"]) if "след" in " ".join(x.get_text(" ",strip=True).split()).lower()
                   or "next" in str(x.attrs).lower() or "page" in str(x.attrs).lower()][:30],
        "forms":[{"attrs":f.attrs,"html":str(f)[:5000]} for f in soup.find_all("form")[-5:]],
    }
print(json.dumps(out,ensure_ascii=False,indent=2))
