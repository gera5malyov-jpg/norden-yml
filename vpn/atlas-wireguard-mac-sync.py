#!/usr/bin/env python3
import base64
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone

import requests

HOME = pathlib.Path.home()
STATE = HOME / ".atlas_wireguard_monitor_state.json"
ID_FILE = HOME / ".atlaspage_real_id"
ROUTER_PASSWORD_FILE = HOME / ".keenetic_chatgpt_pw"
LOG = HOME / "atlas_wireguard_daily_sync.log"

GITHUB_CONTENTS_BASE = "https://api.github.com/repos/gera5malyov-jpg/norden-yml/contents/vpn"
ROUTER = "http://192.168.1.1"
ROUTER_LOGIN = "chatgpt"
PROFILE_RE = re.compile(r"^VPNTYPE-[A-Za-z0-9._-]+\.conf$")


def log(message):
    line = f"[{datetime.now(timezone.utc).isoformat()}] {message}"
    print(line)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch_repo_bytes(filename):
    url = GITHUB_CONTENTS_BASE + "/" + filename
    r = requests.get(
        url,
        params={"ref": "main"},
        timeout=30,
        headers={"Accept": "application/vnd.github+json", "Cache-Control": "no-cache"},
    )
    r.raise_for_status()
    data = r.json()
    content = data.get("content") or ""
    return base64.b64decode(content)


def fetch_repo_json(filename):
    return json.loads(fetch_repo_bytes(filename).decode("utf-8"))


def load_state(current_profiles):
    if STATE.exists():
        try:
            data = json.loads(STATE.read_text(encoding="utf-8"))
            if isinstance(data.get("seen"), list):
                return data
        except Exception:
            pass
    data = {
        "version": 1,
        "seen": sorted(current_profiles),
        "initialized_at": datetime.now(timezone.utc).isoformat(),
        "note": "Fail-safe baseline initialization. Existing profiles are not auto-imported."
    }
    save_state(data)
    log("BASELINE_INITIALIZED count=" + str(len(data["seen"])))
    return data


def save_state(data):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, STATE)


def router_auth():
    if not ROUTER_PASSWORD_FILE.exists():
        raise RuntimeError("router password file is missing")
    password = ROUTER_PASSWORD_FILE.read_text(encoding="utf-8").strip()
    s = requests.Session()
    r = s.get(ROUTER + "/auth", timeout=10)
    realm = r.headers.get("X-NDM-Realm")
    challenge = r.headers.get("X-NDM-Challenge")
    if not realm or not challenge:
        raise RuntimeError("Keenetic auth challenge is missing")
    h1 = hashlib.md5(f"{ROUTER_LOGIN}:{realm}:{password}".encode()).hexdigest()
    h2 = hashlib.sha256((challenge + h1).encode()).hexdigest()
    r = s.post(ROUTER + "/auth", json={"login": ROUTER_LOGIN, "password": h2}, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"Keenetic auth failed: HTTP {r.status_code}")
    return s


def current_wireguard_descriptions(session):
    data = session.get(ROUTER + "/rci/show/interface", timeout=15).json()
    result = {}
    for name, value in data.items():
        if isinstance(value, dict) and str(value.get("type", "")).lower() == "wireguard":
            result[name] = value.get("description") or ""
    return result


def decrypt_pack():
    if not ID_FILE.exists():
        raise RuntimeError("AtlasPage local ID file is missing")
    try:
        packed_b64 = fetch_repo_bytes("atlas-wireguard-pack.enc.b64").decode("ascii").strip()
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            raise RuntimeError("encrypted WireGuard pack is not published yet")
        raise
    encrypted = base64.b64decode(packed_b64)
    with tempfile.TemporaryDirectory(prefix="atlas-wg-") as td:
        td = pathlib.Path(td)
        enc = td / "pack.enc"
        tgz = td / "pack.tar.gz"
        enc.write_bytes(encrypted)
        cmd = [
            "/usr/bin/openssl", "enc", "-d", "-aes-256-cbc", "-salt",
            "-pbkdf2", "-iter", "200000",
            "-pass", "file:" + str(ID_FILE),
            "-in", str(enc), "-out", str(tgz)
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            raise RuntimeError("WireGuard pack decryption failed")
        result = {}
        with tarfile.open(tgz, "r:gz") as tf:
            for member in tf.getmembers():
                name = pathlib.Path(member.name).name
                if not member.isfile() or not PROFILE_RE.fullmatch(name):
                    continue
                src = tf.extractfile(member)
                if src is not None:
                    result[name] = src.read()
        return result


def import_profile(session, filename, raw_bytes):
    description = pathlib.Path(filename).stem
    payload = {
        "import": base64.b64encode(raw_bytes).decode(),
        "name": "",
        "filename": filename,
    }
    r = session.post(
        ROUTER + "/rci/",
        json=[{"interface": {"wireguard": {"import": payload}}}],
        timeout=25,
    )
    r.raise_for_status()
    body = r.json()
    node = {}
    if isinstance(body, list) and body:
        node = (((body[0] or {}).get("interface") or {}).get("wireguard") or {}).get("import") or {}
    created = node.get("created") or ""
    if not created:
        errors = [x.get("message", "") for x in node.get("status", []) if x.get("status") == "error"]
        raise RuntimeError("; ".join(x for x in errors if x) or "Keenetic did not return created interface")

    # New locations are stored disabled. Existing VPN interfaces and policies are untouched.
    down = session.post(ROUTER + "/rci/parse", json=f"interface {created} down", timeout=15)
    if down.status_code != 200:
        log(f"WARNING {filename} created={created} but disable returned HTTP {down.status_code}")
    return created, description


def main():
    manifest = fetch_repo_json("atlas-wireguard-manifest.json")
    current = sorted({x for x in manifest.get("profiles", []) if PROFILE_RE.fullmatch(str(x))})
    if not current:
        raise RuntimeError("manifest contains no valid WireGuard profiles")

    current_hashes = {
        str(k): str(v)
        for k, v in (manifest.get("sha256") or {}).items()
        if str(k) in current and re.fullmatch(r"[0-9a-fA-F]{64}", str(v))
    }

    state = load_state(current)
    seen = set(state.get("seen", []))
    state_hashes = state.get("hashes") if isinstance(state.get("hashes"), dict) else {}

    seeded = []
    for filename in current:
        if filename in seen and filename not in state_hashes and filename in current_hashes:
            state_hashes[filename] = current_hashes[filename]
            seeded.append(filename)
    if seeded:
        state["hashes"] = state_hashes
        state["hash_baseline_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        log("HASH_BASELINE_INITIALIZED count=" + str(len(seeded)))

    new_profiles = [x for x in current if x not in seen]
    updated_profiles = [
        x for x in current
        if x in seen
        and x in current_hashes
        and state_hashes.get(x)
        and state_hashes.get(x) != current_hashes.get(x)
    ]
    pending_profiles = new_profiles + [x for x in updated_profiles if x not in new_profiles]

    if not pending_profiles:
        log("NO_NEW_OR_UPDATED_CONFIGS manifest_count=" + str(len(current)))
        return

    if new_profiles:
        log("NEW_LOCATIONS " + ",".join(new_profiles))
    if updated_profiles:
        log("UPDATED_CONFIGS " + ",".join(updated_profiles))

    pack = decrypt_pack()
    missing = [x for x in pending_profiles if x not in pack]
    if missing:
        raise RuntimeError("encrypted pack is missing: " + ",".join(missing))

    session = router_auth()
    descriptions = current_wireguard_descriptions(session)
    desc_set = set(descriptions.values())
    changed = False

    for filename in new_profiles:
        description = pathlib.Path(filename).stem
        if description in desc_set:
            log(f"ALREADY_IN_ROUTER {filename}")
            seen.add(filename)
            if filename in current_hashes:
                state_hashes[filename] = current_hashes[filename]
            changed = True
            continue
        try:
            created, _ = import_profile(session, filename, pack[filename])
            log(f"ADDED_DISABLED {filename} interface={created}")
            desc_set.add(description)
            seen.add(filename)
            if filename in current_hashes:
                state_hashes[filename] = current_hashes[filename]
            changed = True
        except Exception as e:
            log(f"IMPORT_FAILED {filename} {type(e).__name__}: {e}")

    for filename in updated_profiles:
        try:
            created, _ = import_profile(session, filename, pack[filename])
            log(f"UPDATED_CONFIG_ADDED_DISABLED {filename} interface={created}")
            state_hashes[filename] = current_hashes[filename]
            changed = True
        except Exception as e:
            log(f"UPDATED_CONFIG_IMPORT_FAILED {filename} {type(e).__name__}: {e}")

    if changed:
        save = session.post(
            ROUTER + "/rci/",
            json=[{"system": {"configuration": {"save": {}}}}],
            timeout=15,
        )
        log("CONFIG_SAVE_HTTP " + str(save.status_code))
        state["seen"] = sorted(seen)
        state["hashes"] = dict(sorted(state_hashes.items()))
        state["last_success_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state)

    remaining_new = [x for x in current if x not in seen]
    remaining_updated = [
        x for x in current
        if x in current_hashes
        and state_hashes.get(x)
        and state_hashes.get(x) != current_hashes.get(x)
    ]
    remaining = remaining_new + [x for x in remaining_updated if x not in remaining_new]
    if remaining:
        raise RuntimeError("profiles still pending: " + ",".join(remaining))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL {type(e).__name__}: {e}")
        raise
