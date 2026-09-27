import json, os, smtplib, ssl
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from urllib.request import Request, build_opener, HTTPCookieProcessor
from urllib.error import HTTPError
from http.cookiejar import CookieJar

TO=os.getenv("RECIPIENT","gera5@list.ru")
BASE="https://gpnbonus.ru"
MAP=BASE+"/fuel/refuel-map"
MSK=timezone(timedelta(hours=3))
UA="Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36 Oktan/1.1"

def opener():
    return build_opener(HTTPCookieProcessor(CookieJar()))

def post_json(op,url,payload):
    data=json.dumps(payload,separators=(",",":")).encode()
    req=Request(url,data=data,method="POST",headers={
      "User-Agent":UA,"Accept":"application/json, text/plain, */*",
      "Accept-Language":"ru-RU,ru;q=0.9","Content-Type":"application/json",
      "Origin":BASE,"Referer":MAP,"X-Requested-With":"XMLHttpRequest"})
    with op.open(req,timeout=35) as r:
        return json.loads(r.read().decode("utf-8"))

def list_stations(op):
    payload={"open":False,"wash":False,"AZSShopTypeID":False,
             "services":{"car":{},"payment":{},"person":{},"station":{}}}
    try:
        return post_json(op,BASE+"/api/stations/list",payload)
    except HTTPError as e:
        if e.code not in (403,419): raise
        req=Request(MAP,headers={"User-Agent":UA,"Accept-Language":"ru-RU,ru;q=0.9"})
        with op.open(req,timeout=25) as r: r.read(2048)
        return post_json(op,BASE+"/api/stations/list",payload)

def find_station(payload):
    stations=payload.get("stations") or []
    candidates=[]
    for st in stations:
        p=str(st.get("PNPONumber","")).strip()
        addr=" ".join(str(st.get(k,"")) for k in ("city","address","name")).lower().replace("ё","е")
        if p=="17":
            candidates.append(st)
        elif "московск" in addr and ("46" in addr) and ("петербург" in addr or "спб" in addr):
            candidates.append(st)
    if not candidates: raise RuntimeError("АЗС №17 не найдена в официальном списке Газпромнефти")
    for st in candidates:
        addr=" ".join(str(st.get(k,"")) for k in ("city","address","name")).lower()
        if "московск" in addr and "46" in addr: return st
    return candidates[0]

def detail(op,station):
    sid=station.get("GPNAZSID") or station.get("id")
    if not sid: raise RuntimeError("У АЗС №17 отсутствует внутренний ID")
    return post_json(op,BASE+"/api/stations/"+str(sid),{})

def fuels(detail):
    out={}
    for item in detail.get("data") or []:
        product=item.get("product") or {}
        if isinstance(product,list): product=product[0] if product else {}
        rest=item.get("rest") or {}
        if isinstance(rest,list): rest=rest[0] if rest else {}
        name=str(product.get("shortTitle") or product.get("title") or "").strip()
        if not name: continue
        avail=bool(rest.get("avail"))
        delivery=rest.get("delivery")
        status="ЕСТЬ" if avail else ("ОЖИДАЕТСЯ" if delivery not in (None,False,"no","") else "НЕТ")
        out[name]=status
    return out

def normalize(raw):
    ans={}
    for wanted in ("92","95","G-95","ДТ"):
        val="НЕТ"
        for k,v in raw.items():
            u=k.upper().replace("АИ-","").replace("AI-","").strip()
            if wanted=="ДТ" and u.startswith("ДТ"): val=v
            elif u==wanted.upper(): val=v
        ans[wanted]=val
    return ans

def send(st=None,error=None):
    user=os.environ["GMAIL_SMTP_USER"].strip()
    password="".join(os.environ["GMAIL_APP_PASSWORD"].split())
    now=datetime.now(MSK).strftime("%d.%m.%Y %H:%M МСК")
    if error:
        subject="[АЗС №17] Ошибка проверки"
        body=f"АЗС №17\nСанкт-Петербург, Московское шоссе, 46 к3\n\nПроверено: {now}\nОшибка: {error}"
    else:
        subject="[АЗС №17] Наличие топлива"
        body=("АЗС №17\nСанкт-Петербург, Московское шоссе, 46 к3\n\n"
              f"G-95: {st['G-95']}\n95: {st['95']}\n92: {st['92']}\nДТ: {st['ДТ']}\n\n"
              f"Проверено: {now}\nИсточник: официальная карта Газпромнефти")
    msg=EmailMessage(); msg["From"]=user; msg["To"]=TO; msg["Subject"]=subject; msg.set_content(body)
    ctx=ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com",587,timeout=30) as s:
        s.ehlo(); s.starttls(context=ctx); s.ehlo(); s.login(user,password); s.send_message(msg)

if __name__=="__main__":
    try:
        op=opener(); station=find_station(list_stations(op)); raw=fuels(detail(op,station)); st=normalize(raw)
        print(json.dumps({"station":station,"fuel":st},ensure_ascii=False))
        send(st)
    except Exception as e:
        print("ERROR",type(e).__name__,str(e))
        send(error=f"{type(e).__name__}: {e}")
        raise
