#!/usr/bin/env python3
import base64,json,os
t=(os.getenv("WB_API_TOKEN") or "").strip()
if not t: raise SystemExit("missing")
p=t.split(".")[1]
p += "=" * (-len(p)%4)
d=json.loads(base64.urlsafe_b64decode(p.encode()).decode())
safe={k:d.get(k) for k in ("acc","for","t","s","exp","iat") if k in d}
print(json.dumps(safe,ensure_ascii=False))
