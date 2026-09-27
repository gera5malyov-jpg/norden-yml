#!/usr/bin/env python3
import base64
import hashlib
import html
import json
import os
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

BASE_URL = os.environ["MCP_BASE_URL"].rstrip("/")
LOGIN_PASSWORD = os.environ["LOGIN_PASSWORD"]
STATE_FILE = Path(os.environ.get("STATE_FILE", "/data/state.json"))
ACCESS_TTL = int(os.environ.get("ACCESS_TTL", str(30 * 24 * 3600)))
REFRESH_TTL = int(os.environ.get("REFRESH_TTL", str(180 * 24 * 3600)))
SCOPE = "browser.control"
LOCK = threading.RLock()

STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

def load_state():
    if not STATE_FILE.exists():
        return {"clients": {}, "codes": {}, "tokens": {}, "refresh": {}}
    try:
        data = json.loads(STATE_FILE.read_text())
    except Exception:
        data = {}
    for key in ("clients", "codes", "tokens", "refresh"):
        data.setdefault(key, {})
    return data

STATE = load_state()

def save_state():
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(STATE, separators=(",", ":"), sort_keys=True))
    os.chmod(tmp, 0o600)
    tmp.replace(STATE_FILE)

def cleanup():
    now = time.time()
    for bucket in ("codes", "tokens", "refresh"):
        expired = [k for k, v in STATE[bucket].items() if float(v.get("exp", 0)) < now]
        for k in expired:
            STATE[bucket].pop(k, None)
    if expired:
        save_state()

def allowed_redirect(uri):
    try:
        p = urlparse(uri)
    except Exception:
        return False
    if p.scheme != "https" or not p.hostname:
        return False
    host = p.hostname.lower()
    return host == "chatgpt.com" or host.endswith(".chatgpt.com") or host == "openai.com" or host.endswith(".openai.com")

def allowed_client(client_id):
    if client_id in STATE["clients"]:
        return True
    return client_id.startswith("https://chatgpt.com/oauth/") or client_id == "https://chatgpt.com/oauth/client.json"

def b64url_sha256(value):
    digest = hashlib.sha256(value.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")

def challenge(handler, description="Authentication required"):
    metadata = f'{BASE_URL}/.well-known/oauth-protected-resource'
    handler.send_response(HTTPStatus.UNAUTHORIZED)
    handler.send_header("WWW-Authenticate", f'Bearer resource_metadata="{metadata}", scope="{SCOPE}", error="invalid_token", error_description="{description}"')
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()

class Handler(BaseHTTPRequestHandler):
    server_version = "MegapolisBrowserAuth/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.command} {urlparse(self.path).path} - " + fmt % args, flush=True)

    def send_json(self, status, obj, extra=None):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def read_form(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        parsed = parse_qs(raw, keep_blank_values=True)
        return {k: v[-1] for k, v in parsed.items()}

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        return json.loads(raw or "{}")

    def do_HEAD(self):
        if urlparse(self.path).path == "/health":
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self.send_json(200, {"ok": True})
        if path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            return self.send_json(200, {
                "resource": BASE_URL,
                "authorization_servers": [BASE_URL],
                "scopes_supported": [SCOPE],
                "resource_documentation": BASE_URL + "/"
            })
        if path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
            return self.send_json(200, {
                "issuer": BASE_URL,
                "authorization_endpoint": BASE_URL + "/authorize",
                "token_endpoint": BASE_URL + "/token",
                "registration_endpoint": BASE_URL + "/register",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "scopes_supported": [SCOPE]
            })
        if path == "/authorize":
            q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            vals = {k: v[-1] for k, v in q.items()}
            return self.render_login(vals)
        if path == "/validate":
            return self.validate_token()
        if path == "/":
            body = b"ChatGPT Browser MCP"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/authorize":
            return self.authorize_submit(self.read_form())
        if path == "/token":
            return self.token(self.read_form())
        if path == "/register":
            return self.register(self.read_json())
        if path == "/validate":
            return self.validate_token()
        self.send_error(404)

    def render_login(self, vals, error=""):
        required = ["client_id", "redirect_uri", "response_type", "code_challenge", "code_challenge_method"]
        if any(not vals.get(k) for k in required):
            return self.send_json(400, {"error": "invalid_request", "error_description": "Missing OAuth parameters"})
        if vals.get("response_type") != "code" or vals.get("code_challenge_method") != "S256":
            return self.send_json(400, {"error": "unsupported_request"})
        if not allowed_client(vals["client_id"]) or not allowed_redirect(vals["redirect_uri"]):
            return self.send_json(400, {"error": "invalid_client_or_redirect"})
        hidden = "".join(
            f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(str(v))}">'
            for k, v in vals.items()
        )
        err = f'<p style="color:#b00020">{html.escape(error)}</p>' if error else ""
        page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Browser MCP authorization</title>
<style>body{{font-family:system-ui;max-width:520px;margin:60px auto;padding:24px}}input,button{{width:100%;box-sizing:border-box;padding:12px;margin:8px 0}}button{{font-weight:700}}</style></head>
<body><h2>ChatGPT Browser MCP</h2><p>Разрешить ChatGPT управлять браузером на вашем сервере.</p>{err}
<form method="post" action="/authorize">{hidden}
<label>Пароль владельца</label><input type="password" name="password" autocomplete="current-password" required autofocus>
<button type="submit">Разрешить доступ</button></form></body></html>"""
        raw = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def authorize_submit(self, vals):
        password = vals.pop("password", "")
        if not secrets.compare_digest(password, LOGIN_PASSWORD):
            return self.render_login(vals, "Неверный пароль")
        client_id = vals.get("client_id", "")
        redirect_uri = vals.get("redirect_uri", "")
        if not allowed_client(client_id) or not allowed_redirect(redirect_uri):
            return self.send_json(400, {"error": "invalid_client_or_redirect"})
        if vals.get("code_challenge_method") != "S256" or not vals.get("code_challenge"):
            return self.send_json(400, {"error": "invalid_request"})
        code = secrets.token_urlsafe(32)
        with LOCK:
            cleanup()
            STATE["codes"][code] = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "challenge": vals["code_challenge"],
                "scope": vals.get("scope") or SCOPE,
                "resource": vals.get("resource") or BASE_URL,
                "exp": time.time() + 300,
            }
            save_state()
        params = {"code": code}
        if vals.get("state"):
            params["state"] = vals["state"]
        sep = "&" if "?" in redirect_uri else "?"
        self.send_response(302)
        self.send_header("Location", redirect_uri + sep + urlencode(params))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def register(self, payload):
        redirects = payload.get("redirect_uris") or []
        if not redirects or not all(allowed_redirect(x) for x in redirects):
            return self.send_json(400, {"error": "invalid_redirect_uri"})
        client_id = "client_" + secrets.token_urlsafe(24)
        rec = {
            "redirect_uris": redirects,
            "client_name": payload.get("client_name", "ChatGPT"),
            "created": int(time.time()),
        }
        with LOCK:
            STATE["clients"][client_id] = rec
            save_state()
        return self.send_json(201, {
            "client_id": client_id,
            "client_id_issued_at": rec["created"],
            "redirect_uris": redirects,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        })

    def token(self, form):
        grant = form.get("grant_type", "")
        if grant == "authorization_code":
            return self.exchange_code(form)
        if grant == "refresh_token":
            return self.exchange_refresh(form)
        return self.send_json(400, {"error": "unsupported_grant_type"})

    def exchange_code(self, form):
        code = form.get("code", "")
        verifier = form.get("code_verifier", "")
        with LOCK:
            cleanup()
            rec = STATE["codes"].pop(code, None)
            if rec:
                save_state()
        if not rec:
            return self.send_json(400, {"error": "invalid_grant"})
        if rec["redirect_uri"] != form.get("redirect_uri", ""):
            return self.send_json(400, {"error": "invalid_grant"})
        if form.get("client_id") and form.get("client_id") != rec["client_id"]:
            return self.send_json(400, {"error": "invalid_client"})
        if not verifier or not secrets.compare_digest(b64url_sha256(verifier), rec["challenge"]):
            return self.send_json(400, {"error": "invalid_grant", "error_description": "PKCE verification failed"})
        return self.issue_tokens(rec["client_id"], rec["scope"])

    def exchange_refresh(self, form):
        token = form.get("refresh_token", "")
        with LOCK:
            cleanup()
            rec = STATE["refresh"].get(token)
        if not rec:
            return self.send_json(400, {"error": "invalid_grant"})
        if form.get("client_id") and form.get("client_id") != rec["client_id"]:
            return self.send_json(400, {"error": "invalid_client"})
        return self.issue_tokens(rec["client_id"], rec["scope"])

    def issue_tokens(self, client_id, scope):
        access = secrets.token_urlsafe(40)
        refresh = secrets.token_urlsafe(48)
        now = time.time()
        with LOCK:
            STATE["tokens"][access] = {"client_id": client_id, "scope": scope, "exp": now + ACCESS_TTL}
            STATE["refresh"][refresh] = {"client_id": client_id, "scope": scope, "exp": now + REFRESH_TTL}
            save_state()
        return self.send_json(200, {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": ACCESS_TTL,
            "refresh_token": refresh,
            "scope": scope,
        })

    def validate_token(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return challenge(self)
        token = auth[7:].strip()
        with LOCK:
            cleanup()
            rec = STATE["tokens"].get(token)
        if not rec or float(rec.get("exp", 0)) < time.time():
            return challenge(self, "Invalid or expired token")
        self.send_response(200)
        self.send_header("X-MCP-User", "owner")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8000), Handler)
    print(f"auth server listening on 0.0.0.0:8000 for {BASE_URL}", flush=True)
    server.serve_forever()
