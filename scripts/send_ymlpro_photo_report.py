#!/usr/bin/env python3
"""Email weekly YML Pro external image refresh summary from sanitized shard logs."""
from __future__ import annotations
import json
import os
import re
import smtplib
import ssl
from collections import Counter
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

folder = Path(os.getenv("YML_PHOTO_LOG_DIR", "reports/ymlpro-shards"))
expected = int(os.getenv("EXPECTED_SHARDS", "12"))
files = sorted(folder.glob("shard-*.log"))
result = []
failures = []
summaries = []
seen_shards = set()
for path in files:
    match = re.fullmatch(r"shard-(\d+)\.log", path.name)
    if not match:
        continue
    seen_shards.add(int(match.group(1)))
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("UNIVERSAL_RESULT="):
            try:
                row = json.loads(line.split("=", 1)[1])
                if isinstance(row, dict):
                    result.append(row)
            except (ValueError, TypeError):
                failures.append(f"{path.stem}: malformed result")
        elif line.startswith("UNIVERSAL_PROFILE_ERROR="):
            try:
                row = json.loads(line.split("=", 1)[1])
                failures.append(f"profile {row.get('profile_id', '?')}: {row.get('reason', 'failed')}"[:250])
            except ValueError:
                failures.append(f"{path.stem}: profile error")
        elif line.startswith("UNIVERSAL_RUN_FAILED="):
            failures.append(line[21:250])
        elif line.startswith("UNIVERSAL_SUMMARY="):
            summaries.append(line[18:220])

missing = sorted(set(range(expected)) - seen_shards)
updated = sum(int(row.get("updated") or 0) for row in result)
changes = sum(int(row.get("changed") or 0) for row in result)
unchanged = sum(int(row.get("unchanged") or 0) for row in result)
skipped = [r for r in result if r.get("status") in ("skipped", "blocked", "already_running")]
broken = [r for r in result if r.get("status") == "blocked"]
failed_count = len(failures) + len(missing) + len(skipped)
status = "УСПЕШНО" if failed_count == 0 and len(files) == expected else "ЧАСТИЧНО / ОШИБКИ"
if not result and not failures:
    status = "НЕ ВЫПОЛНЕНО"
mode = os.getenv("YML_PHOTO_MODE", "apply")
if mode != "apply":
    status = "ПРЕДВАРИТЕЛЬНАЯ ПРОВЕРКА: " + status
lines = [
    f"Статус: {status}",
    f"Время отчёта (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
    "Сайт: https://profikompany.ru",
    "Задача: актуализация внешних ссылок на изображения по источникам профилей YML Pro",
    f"Режим: {'обновление' if mode == 'apply' else 'только проверка'}",
    f"Обработано профилей: {len(result)}",
    f"Обновлено карточек: {updated}",
    f"Найдено отличающихся карточек: {changes}",
    f"Уже актуальных карточек: {unchanged}",
    f"Пропущено профилей: {len(skipped)}",
    f"Ошибок: {failed_count}",
    f"Завершено частей: {len(seen_shards)} из {expected}",
    "",
    "Профили (ID, наименование, обновлено, без изменений, результат):",
]
for row in sorted(result, key=lambda r: int(r.get("profile_id") or 0)):
    lines.append(
        f"{row.get('profile_id', '?')} - {str(row.get('profile_name', ''))[:65]}: "
        f"обновлено {row.get('updated', 0)}, без изменений {row.get('unchanged', 0)}, "
        f"статус {row.get('status', '?')}"
        + (f", причина {str(row.get('reason'))[:100]}" if row.get("reason") else "")
    )
if missing:
    lines.extend(["", "Отсутствуют отчёты частей: " + ", ".join(map(str, missing))])
if failures:
    lines.extend(["", "Ошибки:"] + failures[:70])
if len(failures) > 70:
    lines.append(f"... ещё {len(failures) - 70} ошибок")
run_url = os.getenv("RUN_URL", "").strip()
if run_url:
    lines.extend(["", "Полные логи GitHub Actions: " + run_url])
lines.extend(["", "Изменялись только ссылки в кратком описании товаров, без цен, остатков и загрузки изображений на хостинг."])
report_text = "\n".join(lines)
Path("reports").mkdir(exist_ok=True)
Path("reports/ymlpro-photo-email-report.txt").write_text(report_text, encoding="utf-8")
print("YML_PHOTO_EMAIL_SUMMARY=" + json.dumps({
    "status":status,"profiles":len(result),"updated":updated,
    "unchanged":unchanged,"failures":failed_count,"missing_shards":missing,
},ensure_ascii=False),flush=True)

user = os.getenv("SMTP_USER", "").strip()
password = os.getenv("SMTP_PASSWORD", "").strip()
recipient = os.getenv("RECIPIENT", "shop@office-mag.com").strip()
if not user or not password:
    raise RuntimeError("Mail credentials not configured in GitHub Secrets")
msg = EmailMessage()
msg["From"] = user
msg["To"] = recipient
msg["Subject"] = f"[Webasyst / YML Pro] Фото-ссылки: {status}, обновлено {updated}"
msg.set_content(report_text)
error = None
for port in (587, 465):
    try:
        if port == 587:
            with smtplib.SMTP("smtp.gmail.com", port, timeout=30) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(user, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL("smtp.gmail.com", port, timeout=30, context=ssl.create_default_context()) as server:
                server.login(user, password)
                server.send_message(msg)
        error = None
        break
    except Exception as exc:
        error = type(exc).__name__
if error:
    raise RuntimeError(f"Mail report could not be delivered ({error})")
print("YML_PHOTO_EMAIL_SENT=" + recipient,flush=True)
