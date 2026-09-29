#!/usr/bin/env python3
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

PANEL = os.getenv("H1CLOUD_PANEL_URL","http://pl3.h1cloud.net:25076").rstrip("/")
USER = os.getenv("H1CLOUD_PANEL_USERNAME","")
PASSWORD = os.getenv("H1CLOUD_PANEL_PASSWORD","")
API_KEY = os.getenv("H1CLOUD_API_KEY","")
PANEL_TOKEN = os.getenv("H1CLOUD_PANEL_TOKEN","")
MCP_TOKEN = os.getenv("H1CLOUD_MCP_TOKEN","")
MCP_URL = os.getenv("H1CLOUD_MCP_URL","https://my.h1cloud.net/api/v1/mcp")
CLIENT = os.getenv("H1CLOUD_CLIENT_NAME","georgiy-vpn")
DAYS = int(os.getenv("H1CLOUD_CLIENT_DAYS","3650"))

SAFE = Path("h1cloud-safe-result.json")
PRIVATE = Path("h1cloud-private-result.json")

for v in (USER,PASSWORD,API_KEY,PANEL_TOKEN,MCP_TOKEN):
    if v: print(f"::add-mask::{v}")

class E(Exception):
    pass

def call(url, method="GET", headers=None, data=None, timeout=25):
    h={"Accept":"application/json"}
    if headers: h.update(headers)
    body=None
    if data is not None:
        body=json.dumps(data).encode()
        h["Content-Type"]="application/json"
    req=urllib.request.Request(url,data=body,headers=h,method=method)
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            raw=r.read().decode("utf-8","replace")
            return (json.loads(raw) if raw else {}), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw=e.read().decode("utf-8","replace")
        try: obj=json.loads(raw)
        except: obj={"raw":raw[:1000]}
        raise E(str(obj.get("error") or obj.get("message") or f"HTTP {e.code}"))

class Panel:
    def __init__(self):
        self.h=None; self.auth=None
    def url(self,p): return PANEL+"/api"+p
    def probe(self,h):
        obj,_=call(self.url("/status"),headers=h)
        return not (isinstance(obj,dict) and obj.get("error")=="unauthorized")
    def login(self):
        tries=[]
        if PANEL_TOKEN:
            for h,n in (({"Authorization":"Bearer "+PANEL_TOKEN},"panel-token/bearer"),({"x-api-key":PANEL_TOKEN},"panel-token/x-api-key")):
                try:
                    if self.probe(h): self.h,self.auth=h,n; return
                except Exception as e: tries.append(f"{n}:{e}")
        if API_KEY:
            for h,n in (({"x-api-key":API_KEY},"api-key/x-api-key"),({"Authorization":"Bearer "+API_KEY},"api-key/bearer")):
                try:
                    if self.probe(h): self.h,self.auth=h,n; return
                except Exception as e: tries.append(f"{n}:{e}")
        if USER and PASSWORD:
            try:
                obj,_=call(self.url("/auth/login"),"POST",data={"username":USER,"password":PASSWORD})
                t=obj.get("token") if isinstance(obj,dict) else None
                if t:
                    print(f"::add-mask::{t}")
                    h={"Authorization":"Bearer "+t}
                    if self.probe(h): self.h,self.auth=h,"username/password"; return
            except Exception as e: tries.append("login:"+str(e))
        raise E("panel authentication failed; "+"; ".join(tries[-6:]))
    def req(self,m,p,d=None):
        if not self.h: self.login()
        obj,_=call(self.url(p),m,self.h,d)
        if isinstance(obj,dict) and obj.get("ok") is False:
            raise E(str(obj.get("error") or obj.get("message") or "panel error"))
        return obj
    def get(self,p): return self.req("GET",p)
    def post(self,p,d=None): return self.req("POST",p,d or {})
    def patch(self,p,d=None): return self.req("PATCH",p,d or {})

def inbounds(x):
    if isinstance(x,dict) and isinstance(x.get("inbounds"),list): return x["inbounds"]
    return x if isinstance(x,list) else []

def clients(x):
    if isinstance(x,dict) and isinstance(x.get("clients"),list): return x["clients"]
    return x if isinstance(x,list) else []

def mcp_http(payload, session=None):
    h={"Authorization":"Bearer "+MCP_TOKEN,"Accept":"application/json, text/event-stream","Content-Type":"application/json"}
    if session: h["Mcp-Session-Id"]=session
    req=urllib.request.Request(MCP_URL,data=json.dumps(payload).encode(),headers=h,method="POST")
    with urllib.request.urlopen(req,timeout=25) as r:
        raw=r.read().decode("utf-8","replace")
        headers={k.lower():v for k,v in r.headers.items()}
    raw=raw.strip()
    if raw.startswith("data:") or "\ndata:" in raw:
        chunks=[]
        for line in raw.splitlines():
            if line.startswith("data:"):
                val=line[5:].strip()
                if val and val!="[DONE]": chunks.append(val)
        raw=chunks[-1] if chunks else "{}"
    return (json.loads(raw) if raw else {}), headers

def mcp_check():
    if not MCP_TOKEN: return {"configured":False,"ok":False}
    init={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"github-h1cloud","version":"1.0"}}}
    try:
        obj,h=mcp_http(init)
        if isinstance(obj,dict) and obj.get("error"):
            return {"configured":True,"ok":False,"reason":str(obj.get("error"))[:300]}
        session=h.get("mcp-session-id")
        try:
            mcp_http({"jsonrpc":"2.0","method":"notifications/initialized","params":{}},session)
        except Exception:
            pass
        tools_obj,_=mcp_http({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}},session)
        tools=[]
        if isinstance(tools_obj,dict):
            arr=(tools_obj.get("result") or {}).get("tools") or []
            for t in arr:
                if isinstance(t,dict):
                    tools.append({
                        "name":t.get("name"),
                        "description":(t.get("description") or "")[:240],
                        "inputSchema":t.get("inputSchema")
                    })
        return {"configured":True,"ok":True,"tools":tools}
    except Exception as e:
        return {"configured":True,"ok":False,"reason":str(e)[:300]}

def find(lst, protocol, tag=None):
    for x in lst:
        if protocol=="wireguard" and (x.get("protocol")=="wireguard" or x.get("id")=="__wg__"): return x
        if tag and x.get("tag")==tag: return x
    return None

def save(safe, private):
    SAFE.write_text(json.dumps(safe,ensure_ascii=False,indent=2))
    PRIVATE.write_text(json.dumps(private,ensure_ascii=False,indent=2))
    p=os.getenv("GITHUB_STEP_SUMMARY")
    if p:
        lines=["# H1Cloud VPN","",f"- Mode: `{safe.get('mode')}`",f"- Auth: `{safe.get('auth','n/a')}`",f"- MCP: `{'OK' if safe.get('mcp',{}).get('ok') else 'not verified'}`","","## Inbounds"]
        for r in safe.get("results",[]):
            line=f"- **{r.get('protocol')}** — {r.get('state')}"
            if r.get("port"): line+=f", port `{r['port']}`"
            if r.get("note"): line+=f" — {r['note']}"
            lines.append(line)
        if safe.get("client"): lines+=["","## Client",f"- `{CLIENT}` — {safe['client'].get('state')}"]
        Path(p).write_text("\n".join(lines)+"\n")

def inspect(p):
    st=p.get("/status"); al=p.get("/allocations"); ib=inbounds(p.get("/inbounds")); cl=clients(p.get("/clients"))
    return {
        "mode":"inspect","auth":p.auth,"mcp":mcp_check(),
        "node":{k:st.get(k) for k in ("node_name","domain","version","xray_version") if isinstance(st,dict) and k in st},
        "available_ports":len(al.get("available",[])) if isinstance(al,dict) else 0,
        "inbounds":[{"protocol":x.get("protocol"),"tag":x.get("tag"),"port":x.get("port"),"network":x.get("network"),"security":x.get("security"),"enabled":x.get("enabled")} for x in ib],
        "client_exists":any(x.get("name")==CLIENT for x in cl),"results":[]
    }, {"status":st,"allocations":al,"inbounds":ib,"clients":cl}

def apply(p):
    safe,private=inspect(p); safe["mode"]="apply"; results=[]
    try: private["backup"]=p.post("/backups",{}); safe["backup"]="created"
    except Exception as e: safe["backup"]="warning: "+str(e)

    st=p.get("/status")
    al=p.get("/allocations")
    ports=sorted({int(x) for x in (al.get("available",[]) if isinstance(al,dict) else []) if str(x).isdigit()})
    def port():
        return ports.pop(0) if ports else None

    desired=[
      ("vless","gh-vless-reality",lambda q:{"protocol":"vless","port":q,"network":"tcp","security":"reality","tag":"gh-vless-reality","stream":{"sni":"www.cloudflare.com","dest":"www.cloudflare.com:443","fp":"chrome"}}),
      ("wireguard",None,lambda q:{"protocol":"wireguard","port":q}),
      ("hysteria2","gh-hysteria2",lambda q:{"protocol":"hysteria2","port":q,"tag":"gh-hysteria2"}),
      ("shadowsocks","gh-shadowsocks",lambda q:{"protocol":"shadowsocks","port":q,"network":"tcp","security":"none","tag":"gh-shadowsocks","method":"2022-blake3-aes-256-gcm"}),
      ("vmess","gh-vmess",lambda q:{"protocol":"vmess","port":q,"network":"tcp","security":"none","tag":"gh-vmess"}),
    ]
    domain=str(st.get("domain") or "").strip() if isinstance(st,dict) else ""
    if domain and domain.lower() not in ("нет","none","null"):
        desired.append(("trojan","gh-trojan-tls",lambda q:{"protocol":"trojan","port":q,"network":"tcp","security":"tls","tag":"gh-trojan-tls","stream":{"sni":domain,"fp":"chrome"}}))
    else:
        results.append({"protocol":"trojan","state":"skipped","note":"TLS/domain is not configured"})

    for proto,tag,builder in desired:
        ib=inbounds(p.get("/inbounds")); old=find(ib,proto,tag)
        if old:
            results.append({"protocol":proto,"state":"already exists","port":old.get("port")}); continue
        q=port()
        if not q:
            results.append({"protocol":proto,"state":"skipped","note":"no allocated free ports"}); continue
        try:
            private.setdefault("create_responses",[]).append(p.post("/inbounds",builder(q)))
            time.sleep(2)
            new=find(inbounds(p.get("/inbounds")),proto,tag)
            results.append({"protocol":proto,"state":"created","port":(new or {}).get("port",q)})
        except Exception as e:
            results.append({"protocol":proto,"state":"failed","port":q,"note":str(e)})

    ib=inbounds(p.get("/inbounds")); ids=[]; wg=False
    for proto,tag,_ in desired:
        x=find(ib,proto,tag)
        if not x: continue
        if proto=="wireguard" or x.get("id")=="__wg__": wg=True
        elif x.get("id"): ids.append(str(x["id"]))

    cl=clients(p.get("/clients")); old=next((x for x in cl if x.get("name")==CLIENT),None)
    try:
        if old:
            private["client_response"]=p.patch("/clients/"+urllib.parse.quote(CLIENT,safe=""),{"inbound_ids":ids,"wg":wg})
            safe["client"]={"state":"updated"}
        else:
            private["client_response"]=p.post("/create",{"name":CLIENT,"days":DAYS,"manual":1,"inbound_ids":ids,"wg":wg})
            safe["client"]={"state":"created"}
        time.sleep(2)
        private["client"]=next((x for x in clients(p.get("/clients")) if x.get("name")==CLIENT),None)
    except Exception as e:
        safe["client"]={"state":"failed","note":str(e)}
    safe["results"]=results
    private["final_inbounds"]=ib
    return safe,private

def verify(p):
    safe,private=inspect(p); safe["mode"]="verify"; ib=inbounds(p.get("/inbounds")); res=[]
    for proto,tag in [("vless","gh-vless-reality"),("wireguard",None),("hysteria2","gh-hysteria2"),("shadowsocks","gh-shadowsocks"),("vmess","gh-vmess"),("trojan","gh-trojan-tls")]:
        x=find(ib,proto,tag)
        res.append({"protocol":proto,"state":"present" if x else "missing","port":x.get("port") if x else None})
    safe["results"]=res
    safe["client"]={"state":"present" if any(x.get("name")==CLIENT for x in clients(p.get("/clients"))) else "missing"}
    return safe,private

def shape(v, depth=0):
    if depth > 3: return type(v).__name__
    if isinstance(v, dict):
        return {str(k):shape(x,depth+1) for k,x in v.items()}
    if isinstance(v, list):
        return [shape(v[0],depth+1)] if v else []
    return type(v).__name__

def wgprobe(p):
    cl=clients(p.get("/clients"))
    client=next((x for x in cl if x.get("name")==CLIENT),None)
    probes={}
    paths=[
        f"/clients/{urllib.parse.quote(CLIENT,safe='')}",
        f"/clients/{urllib.parse.quote(CLIENT,safe='')}/wireguard",
        f"/clients/{urllib.parse.quote(CLIENT,safe='')}/wg",
        "/wireguard",
        "/wireguard/clients",
        "/wg"
    ]
    for path in paths:
        try:
            obj=p.get(path)
            probes[path]={"ok":True,"shape":shape(obj)}
        except Exception as e:
            probes[path]={"ok":False,"error":str(e)[:160]}

    js_hits=[]
    try:
        html,_=call(PANEL+"/panel/clients",headers=p.h)
        if not isinstance(html,str):
            # call() tries JSON; fall back to raw HTML fetch.
            html=""
    except Exception:
        try:
            req=urllib.request.Request(PANEL+"/panel/clients",headers=p.h)
            with urllib.request.urlopen(req,timeout=25) as r:
                html=r.read().decode("utf-8","replace")
        except Exception:
            html=""
    import re
    assets=re.findall(r'<script[^>]+src=["\']([^"\']+)["\']',html)
    for a in assets[-10:]:
        u=urllib.parse.urljoin(PANEL+"/panel/clients",a)
        try:
            req=urllib.request.Request(u,headers={"Accept":"*/*"})
            with urllib.request.urlopen(req,timeout=25) as r:
                txt=r.read().decode("utf-8","replace")
            low=txt.lower()
            for needle in ["wireguard","__wg__","wg_enabled","/wg","/wireguard"]:
                pos=0
                while True:
                    i=low.find(needle.lower(),pos)
                    if i<0: break
                    sn=txt[max(0,i-180):min(len(txt),i+260)]
                    sn=re.sub(r'\s+',' ',sn)
                    if sn not in js_hits: js_hits.append(sn)
                    pos=i+len(needle)
                    if len(js_hits)>=25: break
                if len(js_hits)>=25: break
        except Exception:
            pass
        if len(js_hits)>=25: break

    safe_client=None
    if client:
        safe_client={
            "wg":bool(client.get("wg")),
            "wg_conf_present":bool(client.get("wg_conf")),
            "wg_conf_length":len(client.get("wg_conf") or ""),
            "inbound_links":[
                {"id":x.get("id"),"tag":x.get("tag"),"protocol":x.get("protocol"),"network":x.get("network"),"security":x.get("security"),"port":x.get("port")}
                for x in (client.get("inbound_links") or []) if isinstance(x,dict)
            ],
            "links_keys":sorted(list((client.get("links") or {}).keys())) if isinstance(client.get("links"),dict) else []
        }
    return {
        "mode":"wgprobe",
        "auth":p.auth,
        "client_state":safe_client,
        "client_shape":shape(client) if client else None,
        "api_probes":probes,
        "frontend_assets":assets[:30],
        "frontend_hits":js_hits[:25],
        "results":[]
    }, {"client":client}

def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "inspect").lower()
    if mode not in ("inspect","apply","verify","wgprobe"): raise E("bad mode")
    p=Panel(); p.login()
    safe,private={"inspect":inspect,"apply":apply,"verify":verify,"wgprobe":wgprobe}[mode](p)
    save(safe,private)
    print(json.dumps(safe,ensure_ascii=False,indent=2))

try:
    main()
except Exception as e:
    fail={"mode":sys.argv[1] if len(sys.argv)>1 else "inspect","fatal":str(e),"mcp":mcp_check()}
    save(fail,{"fatal":str(e)})
    print(json.dumps(fail,ensure_ascii=False,indent=2))
    raise
