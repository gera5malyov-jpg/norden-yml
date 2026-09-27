import os, re, smtplib, ssl
from datetime import datetime
from email.message import EmailMessage
from urllib.request import Request, urlopen

TO=os.getenv("RECIPIENT","gera5@list.ru")
SOURCE="https://xn--90addebmh2bc.xn--p1ai/region/sankt-peterburg/a92"
ADDRESS="Московское шоссе, 46, корп. 3"

def fetch_text():
    req=Request(SOURCE,headers={"User-Agent":"Mozilla/5.0 (compatible; GPN17Monitor/1.0)"})
    with urlopen(req,timeout=20) as r:
        html=r.read().decode("utf-8","replace")
    text=re.sub(r"<script[\\s\\S]*?</script>|<style[\\s\\S]*?</style>"," ",html,flags=re.I)
    text=re.sub(r"<[^>]+>"," ",text)
    text=re.sub(r"&nbsp;"," ",text)
    return re.sub(r"\\s+"," ",text)

def status():
    text=fetch_text()
    pos=text.lower().find(ADDRESS.lower())
    if pos<0:
        raise RuntimeError("АЗС №17 не найдена в источнике")
    block=text[max(0,pos-500):pos+1200]
    def one(names):
        for name in names:
            m=re.search(re.escape(name)+r".{0,80}?(В наличии|Нет в наличии|Нет данных|нет|есть)",block,re.I)
            if m:
                v=m.group(1).lower()
                if "в наличии" in v or v=="есть": return "ЕСТЬ"
                if "нет данных" in v: return "НЕТ ДАННЫХ"
                return "НЕТ"
        return "НЕТ ДАННЫХ"
    return {"АИ-92":one(["АИ-92","Аи-92"]),"АИ-95":one(["АИ-95","Аи-95"]),"ДТ":one(["Дизель","ДТ"])}

def send(st,err=None):
    user=os.environ["GMAIL_SMTP_USER"].strip()
    password="".join(os.environ["GMAIL_APP_PASSWORD"].split())
    now=datetime.now().astimezone().strftime("%d.%m.%Y %H:%M %Z")
    if err:
        subject="[АЗС №17] Ошибка проверки"
        body=f"АЗС №17\nСанкт-Петербург, Московское шоссе, 46 к3\n\nПроверено: {now}\nНе удалось получить актуальные данные: {err}"
    else:
        subject="[АЗС №17] Наличие топлива"
        body=("АЗС №17\nСанкт-Петербург, Московское шоссе, 46 к3\n\n"
              f"АИ-92: {st['АИ-92']}\nАИ-95: {st['АИ-95']}\nДТ: {st['ДТ']}\n"
              "G-95: НЕТ ДАННЫХ\n\n"
              f"Проверено: {now}\nИсточник: ГдеБензин.рф")
    msg=EmailMessage(); msg["From"]=user; msg["To"]=TO; msg["Subject"]=subject; msg.set_content(body)
    ctx=ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com",587,timeout=30) as s:
        s.ehlo(); s.starttls(context=ctx); s.ehlo(); s.login(user,password); s.send_message(msg)

if __name__=="__main__":
    try:
        st=status(); print(st); send(st)
    except Exception as e:
        print("ERROR:",type(e).__name__,str(e))
        send({},f"{type(e).__name__}: {e}")
        raise
