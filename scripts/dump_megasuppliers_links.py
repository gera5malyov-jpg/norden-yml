#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request


VM_ID = 44780
VM_IP = "45.86.180.49"
ROOT = "/home/web/vm-23f9aff9.na4u.ru/www"


def _request_json(url, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def dump_links(output_path, supplier_id=1):
    api_key = os.getenv("NETANGELS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("NETANGELS_API_KEY is not set")

    token_payload = urllib.parse.urlencode({"api_key": api_key}).encode()
    token = _request_json(
        "https://panel.netangels.ru/api/gateway/token/",
        "POST",
        token_payload,
        {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "norden-links-dump/1.0",
        },
    ).get("token")
    if not token:
        raise RuntimeError("NetAngels token missing")

    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "norden-links-dump/1.0",
    }

    with tempfile.TemporaryDirectory() as td:
        key_path = os.path.join(td, "id_ed25519")
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key_path],
            check=True,
        )
        pub = open(key_path + ".pub", encoding="utf-8").read().strip()
        created = _request_json(
            f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/create/",
            "POST",
            json.dumps({
                "key": pub,
                "name": "chatgpt-norden-links-" + os.getenv("GITHUB_RUN_ID", "manual"),
            }).encode(),
            headers,
        )
        key_id = created.get("id")
        if not key_id:
            raise RuntimeError("temporary SSH key id missing")

        try:
            php = r'''<?php
$root='/home/web/vm-23f9aff9.na4u.ru/www';
chdir($root);
require_once $root.'/wa-config/SystemConfig.class.php';
waSystem::getInstance(null,new SystemConfig());
wa('shop');
$m=new waModel();
$supplier_id=(int)getenv('SUPPLIER_ID');
$rows=$m->query(
  "SELECT supplier_sku,product_id,sku_id,purchase_price,stock
   FROM shop_megasuppliers_product
   WHERE supplier_id=i:supplier_id
   ORDER BY id",
  array('supplier_id'=>$supplier_id)
)->fetchAll();
echo json_encode(array('supplier_id'=>$supplier_id,'items'=>$rows),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
?>'''
            remote = (
                "set -eu\n"
                "cat >/tmp/norden_links_dump.php <<'PHP'\n"
                + php
                + "\nPHP\n"
                "chown web:web /tmp/norden_links_dump.php\n"
                f"SUPPLIER_ID={int(supplier_id)} su -s /bin/bash web -c 'SUPPLIER_ID={int(supplier_id)} php /tmp/norden_links_dump.php'\n"
                "rm -f /tmp/norden_links_dump.php\n"
            )
            proc = subprocess.run(
                [
                    "ssh",
                    "-i",
                    key_path,
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=/dev/null",
                    "-o",
                    "ConnectTimeout=15",
                    "root@" + VM_IP,
                    remote,
                ],
                text=True,
                capture_output=True,
                timeout=120,
            )
            if proc.returncode:
                raise RuntimeError(
                    "link dump failed: "
                    + (proc.stderr[-2000:] or proc.stdout[-2000:])
                )
            payload = json.loads(proc.stdout)
            items = payload.get("items") or []
            if not isinstance(items, list):
                raise RuntimeError("invalid supplier links payload")
            with open(output_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            print(json.dumps({
                "supplier_id": supplier_id,
                "links": len(items),
                "output": output_path,
            }, ensure_ascii=False))
        finally:
            try:
                _request_json(
                    f"https://api-ms.netangels.ru/api/v1/cloud/vms/{VM_ID}/ssh/{key_id}/",
                    "DELETE",
                    None,
                    headers,
                )
            except Exception as exc:
                print("temporary SSH key cleanup warning:", exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="norden-links.json")
    ap.add_argument("--supplier-id", type=int, default=1)
    args = ap.parse_args()
    dump_links(args.output, args.supplier_id)


if __name__ == "__main__":
    main()
