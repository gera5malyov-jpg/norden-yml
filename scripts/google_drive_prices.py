#!/usr/bin/env python3
import argparse
import json
import os
import pathlib
import subprocess
import sys

DEFAULT_FOLDER_ID = "1iKh4RGaq1ViyWmHENjp8oeuauHmWEyj9"


def folder_url_from_env() -> str:
    url = os.getenv("GOOGLE_DRIVE_PRICES_FOLDER_URL", "").strip()
    if url:
        return url
    folder_id = os.getenv("GOOGLE_DRIVE_PRICES_FOLDER_ID", DEFAULT_FOLDER_ID).strip()
    return f"https://drive.google.com/drive/folders/{folder_id}"


def run_gdown(args, capture=False):
    cmd = [sys.executable, "-m", "gdown", *args]
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=capture,
    )


def list_public_folder(url: str):
    result = run_gdown([url, "--json", "--quiet"], capture=True)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "неизвестная ошибка").strip()
        raise RuntimeError(f"Не удалось прочитать публичную папку Google Drive: {message}")

    raw = result.stdout.strip()
    items = json.loads(raw or "[]")
    print(f"Google Drive: найдено файлов: {len(items)}")
    for item in items:
        print(f"- {item.get('path', '')}")
    return items


def download_public_folder(url: str, dest: pathlib.Path):
    dest.mkdir(parents=True, exist_ok=True)
    result = run_gdown([url, "-O", str(dest), "--quiet"], capture=True)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "неизвестная ошибка").strip()
        raise RuntimeError(f"Не удалось скачать публичную папку Google Drive: {message}")

    files = sorted(p for p in dest.rglob("*") if p.is_file())
    print(f"Google Drive: скачано файлов: {len(files)}")
    for path in files:
        print(f"- {path.relative_to(dest)}")


def main():
    parser = argparse.ArgumentParser(
        description="Чтение прайсов поставщиков из публичной папки Google Drive без хранения в Git."
    )
    parser.add_argument("--folder-url", default=folder_url_from_env())
    parser.add_argument("--dest", default="runtime/prices")
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()

    if args.list_only:
        list_public_folder(args.folder_url)
        return 0

    download_public_folder(args.folder_url, pathlib.Path(args.dest))
    print("Готово. Прайсы находятся только во временной рабочей директории запуска.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        sys.exit(1)
