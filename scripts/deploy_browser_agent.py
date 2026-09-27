#!/usr/bin/env python3
import base64, json, os, secrets, shlex, socket, subprocess, sys, tempfile, time
import urllib.parse, urllib.request, urllib.error
from pathlib import Path

TARGET_IP = "45.86.180.49"
MCP_HOST = "browser-45-86-180-49.sslip.io"
BASE = Path(__file__).resolve().parents[1]
STATUS = BASE / ".browser-agent" / "status.json"
API_KEY = os.environ.get("NETANGELS_API_KEY", "").strip()
RUN_ID = os.environ.get("GITHUB_RUN_ID", "manual")
PUBLIC_PEM = """-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAoIn//F3hp9ysZCKXu3pA
AAIeEJvOoMeu02f/cYw/TCJVI8gckpu6EeIFKW3r39SAa2dU445qdtM7OsPcO5ce
cHGeRd+eZ5PFW0ffuEeQVUVRQIEs15zM/pAWoj6oK9Jhru0tSZBPBADgwq6ZZ78O
r9YLjHE+hK/Lq+m2zeByWkAgsQgkepHcPYTJZ6nwv6PM61AgAhS42YJbolyu1cVw
S/ATdVZGE2p1uehdg8y10yXQuVUiMPbJoKZEn29Sk3J719lfpdk+bMF69yvvCRtX
xyYekagTaZID4FkcDLJX2QcfXEqFYp/SzU2d7ue32SELv7qPTEZWPHxNBFHOwnEB
IlVrfcBAxBihwssuEmN+Jjirkyal9J5Z9IcqhbbhRyzD5Clw0ZY9kNWBHpwrdTmr
qRne/e4pakqy1D+QHBIKEfx5n6d3xbvcSuXFOCTlRrZH/kgwbzlT9Nwp5HFBKrvI
xvVX9dj1BJcZj8qo9045GIzw2dlB78gnsPneRbraln0MLWVC2klSDi2vFEG742BS
qemBI5JLB7gQ4c7dVdj6g4//GSCdpFeY8/kNwNiACDA3u0rzqLnSXDI+kKZ1NjlJ
sixYcGMgJj4Mg6MHNtl9+VTjun6Q8LBNiqRxWHZ55sSnXwaBOyVxvY4h9eEm+/3C
x3Lupu+jOoqDNgBxdwPS35cCAwEAAQ==
-----END PUBLIC KEY-----
"""

def req_json(url, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8")
        return r.status, json.loads(raw) if raw else {}

def token_and_vm():
    if not API_KEY:
        raise RuntimeError("NETANGELS_API_KEY is not set")
    data = urllib.parse.urlencode({"api_key": API_KEY}).encode()
    _, payload = req_json(
        "https://panel.netangels.ru/api/gateway/token/",
        "POST", {"Content-Type": "application/x-www-form-urlencoded"}, data
    )
    token = payload["token"]
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    _, payload = req_json("https://api-ms.netangels.ru/api/v1/cloud/vms/?limit=100", headers=headers)
    entities = payload.get("entities", payload if isinstance(payload, list) else [])
    vm = next((x for x in entities if str(x.get("main_ip", "")).strip() == TARGET_IP), None)
    if not vm:
        raise RuntimeError("Target VM not found")
    return token, headers, str(vm["id"])

def encrypt_password(password, work):
    pub = work / "status_pub.pem"
    raw = work / "password.txt"
    enc = work / "password.enc"
    pub.write_text(PUBLIC_PEM)
    raw.write_text(password)
    subprocess.run([
        "openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", str(pub),
        "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256",
        "-in", str(raw), "-out", str(enc)
    ], check=True)
    return base64.b64encode(enc.read_bytes()).decode()

def ssh_cmd(key, remote, stdin=None, check=True):
    cmd = [
        "ssh", "-i", str(key), "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=20",
        "root@" + TARGET_IP, remote
    ]
    return subprocess.run(cmd, input=stdin, text=True, check=check, capture_output=True)

def scp(key, src, dest):
    subprocess.run([
        "scp", "-i", str(key), "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=20",
        str(src), "root@" + TARGET_IP + ":" + dest
    ], check=True)

def api_delete_key(headers, vm_id, key_id):
    req = urllib.request.Request(
        "https://api-ms.netangels.ru/api/v1/cloud/vms/" + vm_id + "/ssh/" + str(key_id) + "/",
        method="DELETE", headers=headers
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()

def deploy():
    result = {
        "status": "FAILED",
        "endpoint": "https://" + MCP_HOST + "/mcp",
        "oauth_authorization": "https://" + MCP_HOST + "/authorize",
        "target_ip": TARGET_IP,
        "netangels": "pending",
        "deploy": "pending",
        "verify": "pending",
        "login_password_rsa_oaep_sha256_b64": "",
        "updated_at": "",
        "run_id": RUN_ID,
        "error": ""
    }
    token = headers = vm_id = key_id = None
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        key = work / "id_ed25519"
        try:
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
            password = secrets.token_hex(18)
            print("::add-mask::" + password, flush=True)
            result["login_password_rsa_oaep_sha256_b64"] = encrypt_password(password, work)

            token, headers, vm_id = token_and_vm()
            pubkey = (key.with_suffix(".pub")).read_text().strip()
            body = json.dumps({"key": pubkey, "name": "chatgpt-browser-deploy-" + RUN_ID}).encode()
            _, created = req_json(
                "https://api-ms.netangels.ru/api/v1/cloud/vms/" + vm_id + "/ssh/create/",
                "POST", {**headers, "Content-Type": "application/json"}, body
            )
            key_id = str(created["id"])
            result["netangels"] = "success"

            connected = False
            last_err = ""
            for _ in range(18):
                p = ssh_cmd(key, "echo ssh_ok", check=False)
                if p.returncode == 0:
                    connected = True
                    break
                last_err = p.stderr[-500:]
                time.sleep(5)
            if not connected:
                raise RuntimeError("SSH not available after temporary key upload: " + last_err)

            prep = r"""set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if [ -f /opt/chatgpt-browser/docker-compose.yml ]; then
  cd /opt/chatgpt-browser
  if docker compose version >/dev/null 2>&1; then docker compose down || true
  elif command -v docker-compose >/dev/null 2>&1; then docker-compose down || true
  fi
fi
free_kb="$(df -Pk / | awk 'NR==2 {print $4}')"
if [ "$free_kb" -lt 3500000 ]; then echo "Not enough disk: $free_kb KB free" >&2; exit 43; fi
if ! command -v docker >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y docker.io docker-compose-plugin || apt-get install -y docker.io docker-compose
fi
systemctl enable --now docker
mkdir -p /opt/chatgpt-browser/browser-profile /opt/chatgpt-browser/playwright-output /opt/chatgpt-browser/auth-data /opt/chatgpt-browser/caddy-data /opt/chatgpt-browser/caddy-config
chmod 700 /opt/chatgpt-browser/auth-data
"""
            p = ssh_cmd(key, "bash -s", stdin=prep, check=False)
            if p.returncode != 0:
                raise RuntimeError("Remote preparation failed: " + (p.stderr or p.stdout)[-1000:])

            src = BASE / "infra" / "browser-agent"
            scp(key, src / "auth_server.py", "/opt/chatgpt-browser/auth_server.py")
            scp(key, src / "docker-compose.yml", "/opt/chatgpt-browser/docker-compose.yml")
            scp(key, src / "nginx.conf", "/opt/chatgpt-browser/nginx.conf")
            scp(key, src / "install_nginx_proxy.sh", "/opt/chatgpt-browser/install_nginx_proxy.sh")
            env_text = "MCP_HOST=" + MCP_HOST + "\nLOGIN_PASSWORD=" + password + "\n"
            p = ssh_cmd(key, "umask 077; cat > /opt/chatgpt-browser/.env; chmod 600 /opt/chatgpt-browser/.env", stdin=env_text, check=False)
            if p.returncode != 0:
                raise RuntimeError("Cannot write remote .env: " + p.stderr[-800:])

            up = r"""set -euo pipefail
cd /opt/chatgpt-browser
if docker compose version >/dev/null 2>&1; then
  docker compose pull
  docker compose up -d --remove-orphans
  docker compose ps
else
  docker-compose pull
  docker-compose up -d --remove-orphans
  docker-compose ps
fi
"""
            p = ssh_cmd(key, "bash -s", stdin=up, check=False)
            if p.returncode != 0:
                raise RuntimeError("Docker deployment failed: " + (p.stderr or p.stdout)[-1500:])

            p = ssh_cmd(key, "bash /opt/chatgpt-browser/install_nginx_proxy.sh", check=False)
            if p.returncode != 0:
                raise RuntimeError("Nginx/TLS setup failed: " + (p.stderr or p.stdout)[-1800:])
            result["deploy"] = "success"

            resolved = socket.gethostbyname(MCP_HOST)
            if resolved != TARGET_IP:
                raise RuntimeError("DNS mismatch: " + resolved)

            healthy = False
            last = ""
            for _ in range(60):
                try:
                    with urllib.request.urlopen("https://" + MCP_HOST + "/health", timeout=10) as r:
                        last = r.read().decode()
                        if r.status == 200:
                            healthy = True
                            break
                except Exception as e:
                    last = str(e)
                time.sleep(5)
            if not healthy:
                raise RuntimeError("HTTPS health check failed: " + last)

            try:
                urllib.request.urlopen("https://" + MCP_HOST + "/mcp", timeout=15)
                raise RuntimeError("Anonymous MCP unexpectedly accessible")
            except urllib.error.HTTPError as e:
                if e.code != 401 or "Bearer" not in (e.headers.get("WWW-Authenticate") or ""):
                    raise RuntimeError("MCP auth check failed: HTTP " + str(e.code))

            probe = r"""set -euo pipefail
cd /opt/chatgpt-browser
if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi
$DC exec -T auth python - <<'PY'
import urllib.request
req = urllib.request.Request(
    "http://playwright:8931/mcp",
    data=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"health-probe","version":"1"}}}',
    headers={"Content-Type":"application/json","Accept":"application/json, text/event-stream"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=20) as r:
    print(r.status)
    if r.status != 200:
        raise SystemExit(1)
PY
"""
            p = ssh_cmd(key, "bash -s", stdin=probe, check=False)
            if p.returncode != 0:
                raise RuntimeError("Playwright MCP probe failed: " + (p.stderr or p.stdout)[-1200:])
            result["verify"] = "success"
            result["status"] = "READY"

        except Exception as e:
            result["error"] = str(e)[:2000]
            print("DEPLOY ERROR:", result["error"], file=sys.stderr)
        finally:
            if headers and vm_id and key_id:
                try:
                    api_delete_key(headers, vm_id, key_id)
                    print("temporary SSH key removed")
                except Exception as e:
                    print("WARNING: temporary SSH key cleanup failed:", e, file=sys.stderr)
            result["updated_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
            STATUS.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result["status"] == "READY"

if __name__ == "__main__":
    ok = deploy()
    sys.exit(0 if ok else 1)
