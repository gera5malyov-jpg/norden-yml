import os, re, smtplib, ssl
from datetime import datetime
from email.message import EmailMessage
from urllib.request import Request, urlopen

URL="https://tutbenz.app/azs/5d130823-c9c8-48d1-af6a-924dd248d333"
TO=os.getenv("RECIPIENT","gera5@list.ru")

def get_status():
    req=Request(URL,headers={"User-Agent":"Mozilla/5.0"})
    with urlopen(req,timeout=30) as r:
        text=r.read().decode("utf-8","replace")
    text=re.sub(r"<[^>]+>"," ",text)
    text=re.sub(r"\\s+"," ",text)
    out={}
    for fuel in ("АИ-92","АИ-95","ДТ"):
        m=re.search(re.escape(fuel)+r".{0,180}?(Есть|Нет данных|Нет)",text,re.I)
        out[fuel]=m.group(1).upper() if m else "НЕТ ДАННЫХ"
    return out

def mail(status):
    user=os.environ["GMAIL_SMTP_USER"].strip()
    password="".join(os.environ["GMAIL_APP_PASSWORD"].split())
    body=["АЗС №17 — Санкт-Петербург, Московское шоссе, 46 к3","",
          "АИ-92: "+status["АИ-92"],"АИ-95: "+status["АИ-95"],"ДТ: "+status["ДТ"],"",
          "Проверено: "+datetime.now().astimezone().strftime("%d.%m.%Y %H:%M %Z"),
          "Источник мониторинга: "+URL]
    msg=EmailMessage(); msg["From"]=user; msg["To"]=TO; msg["Subject"]="[АЗС №17] Наличие топлива"; msg.set_content("\n".join(body))
    ctx=ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com",587,timeout=60) as s:
        s.ehlo(); s.starttls(context=ctx); s.ehlo(); s.login(user,password); s.send_message(msg)

if __name__=="__main__":
    status=get_status()
    print(status)
    mail(status)
