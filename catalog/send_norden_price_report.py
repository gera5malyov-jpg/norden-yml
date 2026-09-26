#!/usr/bin/env python3
from __future__ import annotations
import json, os, smtplib, ssl
from email.message import EmailMessage
from pathlib import Path

REPORT_FILE=Path(os.environ.get("PRICE_REPORT_FILE","catalog/norden_price_push_report.json"))
RECIPIENT=os.environ.get("RECIPIENT","shop@office-mag.com").strip()
SMTP_USER=os.environ["SMTP_USER"].strip()
SMTP_PASSWORD=os.environ["SMTP_PASSWORD"].strip()
RUN_URL=os.environ.get("RUN_URL","").strip()
WORKFLOW_NAME=os.environ.get("WORKFLOW_NAME","Norden price update").strip()
SYNC_OUTCOME=os.environ.get("SYNC_OUTCOME","").strip().casefold()

data={}
if REPORT_FILE.exists():
    try:data=json.loads(REPORT_FILE.read_text(encoding="utf-8"))
    except Exception as e:data={"status":"ОШИБКА","report_parse_error":str(e)}
status=str(data.get("status") or ("УСПЕШНО" if SYNC_OUTCOME=="success" else "ЗАВЕРШЕНО С ОШИБКАМИ")).strip()
if SYNC_OUTCOME and SYNC_OUTCOME!="success" and status=="УСПЕШНО":
    status="ЗАВЕРШЕНО С ОШИБКАМИ"

lines=[f"Статус: {status}",f"Процесс: {WORKFLOW_NAME}",f"Отчёт: {REPORT_FILE}"]
if isinstance(data.get("channels"),dict):
    lines.append("")
    lines.append("Каналы:")
    for name,x in data["channels"].items():
        if not isinstance(x,dict):continue
        bits=[f"{name}: {x.get('status','')}"]
        for k in ("targeted","updated","accepted","verified","verified_immediately","errors","api_error_count"):
            if k in x and x.get(k) not in (None,[],{},""):
                val=x[k]
                if isinstance(val,list):val=len(val)
                bits.append(f"{k}={val}")
        lines.append(" — ".join(bits))
for key in ("rows","catalog_rows","updated","price_updates","stock_updates","matched","missing"):
    if key in data: lines.append(f"{key}: {data[key]}")
if data.get("fatal_error"):lines.append("Ошибка: "+str(data["fatal_error"]))
if RUN_URL:lines += ["",f"GitHub run: {RUN_URL}"]

msg=EmailMessage()
msg["Subject"]=f"[Norden] Обновление прайса — {status}"
msg["From"]=SMTP_USER
msg["To"]=RECIPIENT
msg.set_content("\n".join(lines))

sent=False;errors=[]
try:
    with smtplib.SMTP("smtp.gmail.com",587,timeout=30) as s:
        s.starttls(context=ssl.create_default_context());s.login(SMTP_USER,SMTP_PASSWORD);s.send_message(msg);sent=True
except Exception as e:
    errors.append("587:"+repr(e))
if not sent:
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com",465,timeout=30,context=ssl.create_default_context()) as s:
            s.login(SMTP_USER,SMTP_PASSWORD);s.send_message(msg);sent=True
    except Exception as e:errors.append("465:"+repr(e))
if not sent:raise RuntimeError("Не удалось отправить отчёт: "+" | ".join(errors))
print(json.dumps({"status":"УСПЕШНО","recipient":RECIPIENT,"subject":msg["Subject"]},ensure_ascii=False))
