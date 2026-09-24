#!/usr/bin/env python3
import json
import os
from pathlib import Path

from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT / "prompts-library"

DOC_ID = os.environ["GOOGLE_PROMPTS_DOC_ID"].strip()
RAW_CREDS = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"].strip()

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]


def credentials():
    info = json.loads(RAW_CREDS)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def get_doc(session):
    url = f"https://docs.googleapis.com/v1/documents/{DOC_ID}?includeTabsContent=true"
    r = session.get(url, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Google Docs GET failed: HTTP {r.status_code}: {r.text[:2000]}")
    return r.json()


def flatten_tabs(tabs):
    out = []
    for tab in tabs or []:
        out.append(tab)
        out.extend(flatten_tabs(tab.get("childTabs") or []))
    return out


def tab_title(tab):
    return ((tab.get("tabProperties") or {}).get("title") or "").strip()


def tab_id(tab):
    return ((tab.get("tabProperties") or {}).get("tabId") or "").strip()


def tab_end_index(tab):
    content = (((tab.get("documentTab") or {}).get("body") or {}).get("content") or [])
    if not content:
        return 1
    return int(content[-1].get("endIndex") or 1)


def batch_update(session, requests):
    url = f"https://docs.googleapis.com/v1/documents/{DOC_ID}:batchUpdate"
    r = session.post(url, json={"requests": requests}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Google Docs batchUpdate failed: HTTP {r.status_code}: {r.text[:2000]}")
    return r.json()


def read_prompt(path):
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        body = "\n".join(lines[1:]).lstrip()
    else:
        title = path.stem.replace("-", " ").strip()
        body = text
    return title, body.rstrip() + "\n"


def ensure_tab(session, title):
    doc = get_doc(session)
    tabs = flatten_tabs(doc.get("tabs") or [])
    for tab in tabs:
        if tab_title(tab) == title:
            return tab_id(tab)

    batch_update(session, [{
        "addDocumentTab": {
            "tabProperties": {
                "title": title
            }
        }
    }])

    doc = get_doc(session)
    tabs = flatten_tabs(doc.get("tabs") or [])
    for tab in tabs:
        if tab_title(tab) == title:
            return tab_id(tab)
    raise RuntimeError(f"Не удалось создать вкладку: {title}")


def replace_tab_text(session, target_tab_id, text):
    doc = get_doc(session)
    target = None
    for tab in flatten_tabs(doc.get("tabs") or []):
        if tab_id(tab) == target_tab_id:
            target = tab
            break
    if not target:
        raise RuntimeError(f"Вкладка не найдена: {target_tab_id}")

    end_index = tab_end_index(target)
    requests = []
    if end_index > 2:
        requests.append({
            "deleteContentRange": {
                "range": {
                    "tabId": target_tab_id,
                    "startIndex": 1,
                    "endIndex": end_index - 1
                }
            }
        })
    requests.append({
        "insertText": {
            "location": {
                "tabId": target_tab_id,
                "index": 1
            },
            "text": text
        }
    })
    batch_update(session, requests)


def main():
    session = AuthorizedSession(credentials())
    paths = sorted(PROMPTS_DIR.glob("*.md"))
    if not paths:
        print("Нет промтов для синхронизации")
        return 0

    for path in paths:
        title, body = read_prompt(path)
        tid = ensure_tab(session, title)
        replace_tab_text(session, tid, body)
        print(f"Синхронизировано: {title}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
