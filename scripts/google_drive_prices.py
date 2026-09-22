#!/usr/bin/env python3
import argparse
import io
import json
import os
import pathlib
import sys

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

FOLDER_MIME = "application/vnd.google-apps.folder"
SHEETS_MIME = "application/vnd.google-apps.spreadsheet"
DOCS_MIME = "application/vnd.google-apps.document"
SLIDES_MIME = "application/vnd.google-apps.presentation"

EXPORTS = {
    SHEETS_MIME: ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    DOCS_MIME: ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    SLIDES_MIME: ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
}


def credentials_from_env():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("Не задан секрет GOOGLE_SERVICE_ACCOUNT_JSON")
    info = json.loads(raw)
    return service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )


def safe_name(name: str) -> str:
    return name.replace("/", "_").replace("\\", "_").strip() or "unnamed"


def list_children(service, folder_id):
    q = f"'{folder_id}' in parents and trashed = false"
    token = None
    while True:
        resp = service.files().list(
            q=q,
            pageSize=1000,
            pageToken=token,
            fields="nextPageToken,files(id,name,mimeType,modifiedTime,size,md5Checksum)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        for item in resp.get("files", []):
            yield item
        token = resp.get("nextPageToken")
        if not token:
            break


def walk(service, folder_id, rel=""):
    for item in list_children(service, folder_id):
        name = safe_name(item["name"])
        item_rel = str(pathlib.PurePosixPath(rel) / name)
        if item["mimeType"] == FOLDER_MIME:
            yield {"kind": "folder", "path": item_rel, **item}
            yield from walk(service, item["id"], item_rel)
        else:
            yield {"kind": "file", "path": item_rel, **item}


def download_file(service, item, dest_root: pathlib.Path):
    out = dest_root / item["path"]
    out.parent.mkdir(parents=True, exist_ok=True)

    mime = item["mimeType"]
    if mime in EXPORTS:
        export_mime, suffix = EXPORTS[mime]
        if not out.name.lower().endswith(suffix):
            out = out.with_name(out.name + suffix)
        request = service.files().export_media(fileId=item["id"], mimeType=export_mime)
    else:
        request = service.files().get_media(fileId=item["id"], supportsAllDrives=True)

    with io.FileIO(out, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return out


def main():
    p = argparse.ArgumentParser(description="Чтение прайсов поставщиков из Google Drive без хранения в Git.")
    p.add_argument("--folder-id", default=os.getenv("GOOGLE_DRIVE_PRICES_FOLDER_ID"))
    p.add_argument("--dest", default="runtime/prices")
    p.add_argument("--list-only", action="store_true")
    args = p.parse_args()

    if not args.folder_id:
        raise SystemExit("Не задан GOOGLE_DRIVE_PRICES_FOLDER_ID или --folder-id")

    service = build("drive", "v3", credentials=credentials_from_env(), cache_discovery=False)

    files = [x for x in walk(service, args.folder_id) if x["kind"] == "file"]
    print(f"Google Drive: найдено файлов: {len(files)}")
    for item in files:
        print(f"- {item['path']} | {item['mimeType']} | {item.get('modifiedTime','')}")

    if args.list_only:
        return 0

    dest = pathlib.Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    for item in files:
        path = download_file(service, item, dest)
        print(f"Скачан: {path}")
    print(f"Готово. Файлы доступны только в рабочей директории запуска: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
