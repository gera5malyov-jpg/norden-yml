import hashlib
import hmac
import json
import os
import urllib.request


class BridgeError(RuntimeError):
    pass


class MegasuppliersBridge:
    def __init__(self, url=None, secret=None, timeout=60):
        self.url = (url or os.getenv("MEGASUPPLIERS_BRIDGE_URL") or "").strip()
        self.secret = (secret or os.getenv("MEGASUPPLIERS_CALLBACK_SECRET") or "").strip()
        self.timeout = int(timeout)
        if not self.url:
            raise BridgeError("MEGASUPPLIERS_BRIDGE_URL is not set")
        if not self.secret:
            raise BridgeError("MEGASUPPLIERS_CALLBACK_SECRET is not set")

    def call(self, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        signature = "sha256=" + hmac.new(self.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-Megasuppliers-Signature": signature,
                "User-Agent": "Megasuppliers-Supplier-Engine/1.1",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except Exception as exc:
            raise BridgeError("Megasuppliers bridge request failed: %s" % exc) from exc
        try:
            data = json.loads(raw) if raw else {}
        except ValueError as exc:
            raise BridgeError("Megasuppliers bridge returned invalid JSON") from exc
        if isinstance(data, dict) and data.get("status") == "fail":
            raise BridgeError("Megasuppliers bridge error: %s" % data.get("errors"))
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if not isinstance(data, dict):
            raise BridgeError("Megasuppliers bridge returned unexpected payload")
        return data

    def get_links(self, supplier_id, request_id, page_size=1000):
        out = []
        offset = 0
        while True:
            data = self.call({
                "action": "links",
                "supplier_id": int(supplier_id),
                "request_id": str(request_id),
                "offset": offset,
                "limit": int(page_size),
            })
            rows = data.get("items") or []
            if not isinstance(rows, list):
                raise BridgeError("Megasuppliers bridge links payload is invalid")
            out.extend(row for row in rows if isinstance(row, dict))
            if len(rows) < page_size:
                break
            offset += len(rows)
        return out

    def sync_links(self, supplier_id, request_id, mappings, chunk_size=300):
        total = 0
        mappings = list(mappings or [])
        for offset in range(0, len(mappings), chunk_size):
            chunk = mappings[offset:offset + chunk_size]
            data = self.call({
                "action": "sync_links",
                "supplier_id": int(supplier_id),
                "request_id": str(request_id),
                "items": chunk,
            })
            total += int(data.get("updated") or 0)
        return total
