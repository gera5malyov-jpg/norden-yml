#!/usr/bin/env python3
import json, os, sys, urllib.parse, urllib.request, urllib.error, socket, ssl

API_KEY = os.environ.get("NETANGELS_API_KEY", "").strip()
if not API_KEY:
    print("ERROR: NETANGELS_API_KEY is not set")
    sys.exit(2)

def post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type":"application/x-www-form-urlencoded",
                                          "User-Agent":"megapolis-netangels-mail-diagnostic/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def get_json(path):
    req = urllib.request.Request("https://api-ms.netangels.ru" + path,
                                 headers={"Authorization": f"Bearer {TOKEN}",
                                          "Accept":"application/json",
                                          "User-Agent":"megapolis-netangels-mail-diagnostic/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

TOKEN = post_form("https://panel.netangels.ru/api/gateway/token/", {"api_key": API_KEY}).get("token")
if not TOKEN:
    print("ERROR: token_not_received")
    sys.exit(3)

print("token_ok=true")

# Mail account status
try:
    mail_user = get_json("/api/v2/mail/")
    print("mail_user_state=", mail_user.get("state"))
    print("mail_user_login=", mail_user.get("login",""))
except Exception as e:
    print("mail_user_error=", type(e).__name__)

# Mail domains + mailboxes
try:
    domains = get_json("/api/v2/mail/domains/?limit=100")
    ents = domains.get("entities", [])
    print("mail_domains_count=", len(ents))
    for d in ents:
        did = d.get("id")
        print("MAIL_DOMAIN", json.dumps({
            "id": did, "name": d.get("name"), "state": d.get("state"),
            "validated": d.get("validated"), "quota": d.get("quota"),
            "used": d.get("used"), "has_dkim": bool(d.get("dkim"))
        }, ensure_ascii=False))
        try:
            boxes = get_json(f"/api/v2/mail/domains/{did}/mailboxes/?limit=100").get("entities", [])
            for b in boxes:
                print("MAILBOX", json.dumps({
                    "id": b.get("id"), "domain_id": did, "name": b.get("name"),
                    "state": b.get("state"), "is_local": b.get("is_local"),
                    "quota": b.get("quota"), "used": b.get("used"),
                    "aliases": b.get("aliases")
                }, ensure_ascii=False))
        except Exception as e:
            print("mailboxes_error", did, type(e).__name__)
except Exception as e:
    print("mail_domains_error=", type(e).__name__)

# Hosting + sites
try:
    containers = get_json("/api/v2/hosting/containers/?limit=100").get("entities", [])
    print("containers_count=", len(containers))
    for c in containers:
        cid = c.get("id")
        print("CONTAINER", json.dumps({"id":cid,"name":c.get("name"),"state":c.get("state"),"login":c.get("login")}, ensure_ascii=False))
        try:
            sites = get_json(f"/api/v2/hosting/containers/{cid}/virtualhosts/?limit=100").get("entities", [])
            for s in sites:
                print("SITE", json.dumps({
                    "id": s.get("id"), "container_id":cid, "name":s.get("name"),
                    "main_alias":s.get("main_alias"), "aliases":s.get("aliases"),
                    "engine":s.get("engine"), "engine_version":s.get("engine_version"),
                    "state":s.get("state"), "cron_mail":s.get("cron_mail")
                }, ensure_ascii=False))
        except Exception as e:
            print("sites_error", cid, type(e).__name__)
except Exception as e:
    print("hosting_error=", type(e).__name__)

# DNS zones and mail-related records
try:
    zones = get_json("/api/v1/dns/zones/?limit=100").get("entities", [])
    print("dns_zones_count=", len(zones))
    for z in zones:
        zid, zname = z.get("id"), z.get("name")
        try:
            recs = get_json(f"/api/v1/dns/zones/{zid}/records/?limit=100").get("entities", [])
            mailrecs = []
            for r in recs:
                if r.get("type") in ("MX","TXT"):
                    mailrecs.append({"name":r.get("name"),"type":r.get("type"),"details":r.get("details")})
            print("DNS_MAIL", json.dumps({"zone":zname,"records":mailrecs}, ensure_ascii=False))
        except Exception as e:
            print("dns_records_error", zname, type(e).__name__)
except Exception as e:
    print("dns_error=", type(e).__name__)

# Connectivity only; no credentials are used or printed.
for host, port, mode in [("mail.netangels.ru",587,"starttls"),("mail.netangels.ru",465,"tls")]:
    try:
        if mode == "tls":
            ctx = ssl.create_default_context()
            with socket.create_connection((host,port), timeout=10) as raw:
                with ctx.wrap_socket(raw, server_hostname=host) as s:
                    print(f"smtp_connect_{port}=true")
        else:
            with socket.create_connection((host,port), timeout=10):
                print(f"smtp_connect_{port}=true")
    except Exception as e:
        print(f"smtp_connect_{port}=false:{type(e).__name__}")
