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
    # Search the panel HTML itself too: some H1 builds inline the frontend bundle.
    low_html=html.lower()
    for needle in ["wg_conf","wireguard","__wg__","wg_enabled","wg:","wg="]:
        pos=0
        while True:
            i=low_html.find(needle.lower(),pos)
            if i<0: break
            sn=html[max(0,i-220):min(len(html),i+320)]
            sn=re.sub(r'\s+',' ',sn)
            if sn not in js_hits: js_hits.append(sn)
            pos=i+len(needle)
            if len(js_hits)>=25: break
        if len(js_hits)>=25: break
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

    sub_probe=None
    if client:
        suburl=client.get("sub_url") or client.get("subscription_url") or client.get("link")
        if suburl:
            try:
                req=urllib.request.Request(suburl,headers={"Accept":"*/*"})
                with urllib.request.urlopen(req,timeout=25) as r:
                    body=r.read().decode("utf-8","replace")
                import base64
                decoded=body
                try:
                    pad="="*((4-len(body.strip())%4)%4)
                    cand=base64.b64decode(body.strip()+pad).decode("utf-8","replace")
                    if cand.strip(): decoded=cand
                except Exception:
                    pass
                low=decoded.lower()
                sub_probe={
                    "http_status":"ok",
                    "length":len(decoded),
                    "has_wireguard_scheme":("wireguard://" in low or "wg://" in low),
                    "has_wireguard_conf":("[interface]" in low and "[peer]" in low),
                    "has_vless":("vless://" in low),
                    "has_hysteria2":("hysteria2://" in low or "hy2://" in low),
                    "line_count":len([x for x in decoded.splitlines() if x.strip()])
                }
            except Exception as e:
                sub_probe={"http_status":"error","error":str(e)[:160]}
    safe_client=None
    if client:
        safe_client={
            "wg":bool(client.get("wg")),
            "wg_conf_present":bool(client.get("wg_conf")),
            "wg_conf_length":len(client.get("wg_conf") or ""),
            "subscription_probe":sub_probe,
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

def wgfix(p):
    # Preserve all non-WireGuard inbound assignments and only toggle WG off/on,
    # matching the panel's own edit-client flow.
    cl=clients(p.get("/clients"))
    client=next((x for x in cl if x.get("name")==CLIENT),None)
    if not client:
        raise E("client not found")
    ids=[str(x.get("id")) for x in (client.get("inbound_links") or []) if isinstance(x,dict) and x.get("id")]
    before={"wg":bool(client.get("wg")),"wg_conf_present":bool(client.get("wg_conf")),"wg_conf_length":len(client.get("wg_conf") or ""),"inbound_ids":ids}
    try:
        backup=p.post("/backups",{})
        backup_state="created"
    except Exception as e:
        backup_state="warning: "+str(e)[:160]
    p.patch("/clients/"+urllib.parse.quote(CLIENT,safe=""),{"wg":False,"inbound_ids":ids})
    time.sleep(2)
    p.patch("/clients/"+urllib.parse.quote(CLIENT,safe=""),{"wg":True,"inbound_ids":ids})
    time.sleep(4)
    cl2=clients(p.get("/clients"))
    c2=next((x for x in cl2 if x.get("name")==CLIENT),None)
    after={"wg":bool(c2.get("wg")) if c2 else False,"wg_conf_present":bool(c2.get("wg_conf")) if c2 else False,"wg_conf_length":len((c2 or {}).get("wg_conf") or ""),"inbound_links":[{"id":x.get("id"),"tag":x.get("tag"),"protocol":x.get("protocol"),"port":x.get("port")} for x in ((c2 or {}).get("inbound_links") or []) if isinstance(x,dict)]}
    return {"mode":"wgfix","auth":p.auth,"backup":backup_state,"before":before,"after":after,"results":[]},{"client":c2}


def wgtest(p):
    name="wg-probe-temp"
    # Clean stale probe if any.
    try:
        p.req("DELETE","/clients/"+urllib.parse.quote(name,safe=""))
    except Exception:
        pass
    created=None
    create_shape=None
    create_wg_conf=False
    create_wg_len=0
    try:
        created=p.post("/create",{"name":name,"days":1,"manual":1,"inbound_ids":[],"wg":True})
        create_shape=shape(created)
        # Inspect only presence/length, never expose config material.
        def find_conf(v):
            if isinstance(v,dict):
                for k,x in v.items():
                    if k=="wg_conf" and isinstance(x,str):
                        return x
                    y=find_conf(x)
                    if y: return y
            elif isinstance(v,list):
                for x in v:
                    y=find_conf(x)
                    if y: return y
            return ""
        conf=find_conf(created)
        create_wg_conf=bool(conf)
        create_wg_len=len(conf)
        time.sleep(2)
        cl=clients(p.get("/clients"))
        c=next((x for x in cl if x.get("name")==name),None)
        after={"exists":bool(c),"wg":bool((c or {}).get("wg")),"wg_conf_present":bool((c or {}).get("wg_conf")),"wg_conf_length":len((c or {}).get("wg_conf") or "")}
    finally:
        try:
            p.req("DELETE","/clients/"+urllib.parse.quote(name,safe=""))
        except Exception:
            pass
    return {"mode":"wgtest","auth":p.auth,"create_response_shape":create_shape,"create_response_wg_conf_present":create_wg_conf,"create_response_wg_conf_length":create_wg_len,"stored_client":after,"deleted":True,"results":[]},{"created":created}


def mcp_open():
    if not MCP_TOKEN:
        raise E("MCP token is not configured")
    init={"jsonrpc":"2.0","id":101,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"github-h1cloud-repair","version":"1.0"}}}
    obj,h=mcp_http(init)
    if isinstance(obj,dict) and obj.get("error"):
        raise E("MCP initialize failed: "+str(obj.get("error"))[:200])
    session=h.get("mcp-session-id")
    try:
        mcp_http({"jsonrpc":"2.0","method":"notifications/initialized","params":{}},session)
    except Exception:
        pass
    return session

def mcp_tool(name,args=None,session=None,req_id=102):
    if session is None: session=mcp_open()
    obj,_=mcp_http({"jsonrpc":"2.0","id":req_id,"method":"tools/call","params":{"name":name,"arguments":args or {}}},session)
    if isinstance(obj,dict) and obj.get("error"):
        raise E("MCP tool "+name+" failed: "+str(obj.get("error"))[:300])
    result=(obj or {}).get("result") if isinstance(obj,dict) else obj
    # Convert text content to JSON when possible.
    if isinstance(result,dict):
        content=result.get("content")
        if isinstance(content,list):
            texts=[]
            for item in content:
                if isinstance(item,dict) and isinstance(item.get("text"),str):
                    texts.append(item["text"])
            if texts:
                joined="\n".join(texts).strip()
                for candidate in (joined, texts[-1].strip()):
                    try:
                        return json.loads(candidate)
                    except Exception:
                        pass
                return {"text":joined}
    return result

def walk_dicts(v):
    out=[]
    if isinstance(v,dict):
        out.append(v)
        for x in v.values(): out.extend(walk_dicts(x))
    elif isinstance(v,list):
        for x in v: out.extend(walk_dicts(x))
    return out

def choose_server_id(v):
    candidates=[]
    for d in walk_dicts(v):
        sid=d.get("id") if isinstance(d,dict) else None
        if isinstance(sid,bool): continue
        if isinstance(sid,(int,str)) and str(sid).isdigit():
            blob=" ".join(str(d.get(k) or "") for k in ("name","server_name","domain","host","hostname","node","location")).lower()
            score=0
            if "pl3.h1cloud.net" in blob: score+=10
            if "pl3" in blob: score+=5
            if "h1cloud" in blob: score+=1
            candidates.append((score,int(sid),blob))
    if not candidates:
        raise E("Could not determine H1Cloud server id")
    candidates.sort(reverse=True)
    if candidates[0][0]>0: return candidates[0][1]
    unique=sorted(set(x[1] for x in candidates))
    if len(unique)==1: return unique[0]
    raise E("Multiple H1Cloud servers found and none matched pl3.h1cloud.net")

def wait_panel(seconds=300):
    deadline=time.time()+seconds
    last=""
    while time.time()<deadline:
        try:
            p=Panel(); p.login()
            st=p.get("/status")
            return p,st
        except Exception as e:
            last=str(e)
            time.sleep(10)
    raise E("Panel did not recover after restart: "+last[:180])

def client_state(p):
    c=next((x for x in clients(p.get("/clients")) if x.get("name")==CLIENT),None)
    return c,{
        "exists":bool(c),
        "wg":bool((c or {}).get("wg")),
        "wg_conf_present":bool((c or {}).get("wg_conf")),
        "wg_conf_length":len((c or {}).get("wg_conf") or ""),
        "inbounds":[
            {"id":x.get("id"),"tag":x.get("tag"),"protocol":x.get("protocol"),"port":x.get("port")}
            for x in ((c or {}).get("inbound_links") or []) if isinstance(x,dict)
        ]
    }

def restartrepair(p):
    safe={"mode":"restartrepair","auth":p.auth,"results":[]}
    private={}
    try:
        private["backup_before_restart"]=p.post("/backups",{})
        safe["backup"]="created"
    except Exception as e:
        safe["backup"]="warning: "+str(e)[:160]

    before_ib=inbounds(p.get("/inbounds"))
    wg_before=find(before_ib,"wireguard",None)
    wg_port=int((wg_before or {}).get("port") or 25078)
    safe["before_inbounds"]=[
        {"protocol":x.get("protocol"),"tag":x.get("tag"),"port":x.get("port"),"enabled":x.get("enabled")}
        for x in before_ib
    ]

    session=mcp_open()
    servers=mcp_tool("list_servers",{},session,103)
    private["server_list_shape"]=shape(servers)
    server_id=choose_server_id(servers)
    safe["server_id"]=server_id

    mcp_tool("server_power",{"server_id":server_id,"action":"restart"},session,104)
    safe["restart"]="requested"
    time.sleep(12)

    p2,st=wait_panel(300)
    safe["panel_recovered"]=True
    safe["node"]={k:st.get(k) for k in ("node_name","domain","version","xray_version") if isinstance(st,dict) and k in st}

    ib=inbounds(p2.get("/inbounds"))
    for proto,tag in [("vless","gh-vless-reality"),("wireguard",None),("hysteria2","gh-hysteria2")]:
        x=find(ib,proto,tag)
        safe["results"].append({"protocol":proto,"state":"present" if x else "missing","port":x.get("port") if x else None,"enabled":x.get("enabled") if x else None})

    c,state=client_state(p2)
    safe["client_after_restart"]=state
    if state["wg_conf_present"]:
        safe["wireguard_repair"]="not needed"
        return safe,private

    # Recreate only WireGuard inbound on the same allocated port.
    safe["wireguard_repair"]="started"
    if wg_before:
        wg_id=str(wg_before.get("id") or "__wg__")
        try:
            p2.req("DELETE","/inbounds/"+urllib.parse.quote(wg_id,safe=""))
            safe["wireguard_deleted"]=True
        except Exception as e:
            safe["wireguard_deleted"]="warning: "+str(e)[:160]
    time.sleep(3)

    allocations=p2.get("/allocations")
    available=[int(x) for x in (allocations.get("available",[]) if isinstance(allocations,dict) else []) if str(x).isdigit()]
    if wg_port not in available:
        # The host can need a short delay to release the UDP allocation.
        time.sleep(5)
        allocations=p2.get("/allocations")
        available=[int(x) for x in (allocations.get("available",[]) if isinstance(allocations,dict) else []) if str(x).isdigit()]
    if wg_port not in available:
        raise E("WireGuard port was not released after deleting inbound")

    p2.post("/inbounds",{"protocol":"wireguard","port":wg_port})
    time.sleep(3)
    safe["wireguard_recreated"]=True

    # Restart panel/Xray service so the host-side WG state is reloaded too.
    try:
        p2.post("/server/restart",{})
        safe["service_restart"]="requested"
        time.sleep(8)
        p2,_=wait_panel(120)
    except Exception as e:
        safe["service_restart"]="warning: "+str(e)[:160]

    c,state=client_state(p2)
    ids=[str(x.get("id")) for x in ((c or {}).get("inbound_links") or []) if isinstance(x,dict) and x.get("id")]
    if c:
        p2.patch("/clients/"+urllib.parse.quote(CLIENT,safe=""),{"wg":False,"inbound_ids":ids})
        time.sleep(2)
        p2.patch("/clients/"+urllib.parse.quote(CLIENT,safe=""),{"wg":True,"inbound_ids":ids})
        time.sleep(5)

    c2,state2=client_state(p2)
    safe["client_after_repair"]=state2
    ib2=inbounds(p2.get("/inbounds"))
    safe["final_inbounds"]=[
        {"protocol":x.get("protocol"),"tag":x.get("tag"),"port":x.get("port"),"network":x.get("network"),"security":x.get("security"),"enabled":x.get("enabled")}
        for x in ib2
    ]
    safe["wireguard_ready"]=bool(state2["wg"] and state2["wg_conf_present"])
    return safe,private


def subscription_flags(client):
    suburl=(client or {}).get("sub_url") or (client or {}).get("subscription_url") or (client or {}).get("link")
    out={"reachable":False,"line_count":0,"vless":False,"hysteria2":False,"shadowsocks":False,"wireguard":False}
    if not suburl: return out
    try:
        req=urllib.request.Request(suburl,headers={"Accept":"*/*"})
        with urllib.request.urlopen(req,timeout=25) as r:
            body=r.read().decode("utf-8","replace")
        import base64
        decoded=body
        try:
            pad="="*((4-len(body.strip())%4)%4)
            cand=base64.b64decode(body.strip()+pad).decode("utf-8","replace")
            if cand.strip(): decoded=cand
        except Exception:
            pass
        low=decoded.lower()
        out.update({
            "reachable":True,
            "line_count":len([x for x in decoded.splitlines() if x.strip()]),
            "vless":"vless://" in low,
            "hysteria2":("hysteria2://" in low or "hy2://" in low),
            "shadowsocks":"ss://" in low,
            "wireguard":("wireguard://" in low or "wg://" in low or ("[interface]" in low and "[peer]" in low))
        })
    except Exception as e:
        out["error"]=str(e)[:160]
    return out

def replacewg(p):
    safe={"mode":"replacewg","auth":p.auth,"results":[]}
    private={}
    try:
        private["backup"]=p.post("/backups",{})
        safe["backup"]="created"
    except Exception as e:
        safe["backup"]="warning: "+str(e)[:160]

    ib=inbounds(p.get("/inbounds"))
    wg=find(ib,"wireguard",None)
    if not wg:
        raise E("WireGuard inbound not found")
    port=int(wg.get("port") or 25078)
    c,cs=client_state(p)
    if not c:
        raise E("client not found")
    keep_ids=[str(x.get("id")) for x in (c.get("inbound_links") or []) if isinstance(x,dict) and x.get("id")]
    safe["before"]={"wireguard_port":port,"client":cs,"subscription":subscription_flags(c)}

    wg_id=str(wg.get("id") or "__wg__")
    p.req("DELETE","/inbounds/"+urllib.parse.quote(wg_id,safe=""))
    time.sleep(4)

    al=p.get("/allocations")
    available=[int(x) for x in (al.get("available",[]) if isinstance(al,dict) else []) if str(x).isdigit()]
    if port not in available:
        time.sleep(5)
        al=p.get("/allocations")
        available=[int(x) for x in (al.get("available",[]) if isinstance(al,dict) else []) if str(x).isdigit()]
    if port not in available:
        raise E("WireGuard port did not become available")

    created=p.post("/inbounds",{
        "protocol":"shadowsocks",
        "port":port,
        "network":"tcp",
        "security":"none",
        "tag":"gh-shadowsocks",
        "method":"2022-blake3-aes-256-gcm"
    })
    private["create_shadowsocks"]=created
    time.sleep(4)

    ib2=inbounds(p.get("/inbounds"))
    ss=find(ib2,"shadowsocks","gh-shadowsocks")
    if not ss:
        raise E("Shadowsocks inbound was not created")
    ssid=str(ss.get("id"))
    ids=list(dict.fromkeys(keep_ids+[ssid]))
    p.patch("/clients/"+urllib.parse.quote(CLIENT,safe=""),{"wg":False,"inbound_ids":ids})
    time.sleep(5)

    c2,cs2=client_state(p)
    sub=subscription_flags(c2)
    safe["after"]={
        "client":cs2,
        "subscription":sub,
        "shadowsocks":{"port":ss.get("port"),"enabled":ss.get("enabled"),"method":ss.get("method"),"tag":ss.get("tag")}
    }
    safe["results"]=[
        {"protocol":"vless","state":"present" if find(ib2,"vless","gh-vless-reality") else "missing","port":25077},
        {"protocol":"shadowsocks","state":"present" if ss else "missing","port":port},
        {"protocol":"hysteria2","state":"present" if find(ib2,"hysteria2","gh-hysteria2") else "missing","port":25079}
    ]
    safe["ready"]=bool(sub.get("vless") and sub.get("hysteria2") and sub.get("shadowsocks"))
    return safe,private


def restorewg(p):
    safe={"mode":"restorewg","auth":p.auth,"results":[]}
    private={}
    try:
        private["backup"]=p.post("/backups",{})
        safe["backup"]="created"
    except Exception as e:
        safe["backup"]="warning: "+str(e)[:160]

    ib=inbounds(p.get("/inbounds"))
    ss=find(ib,"shadowsocks","gh-shadowsocks")
    port=int((ss or {}).get("port") or 25078)

    c,cs=client_state(p)
    if not c:
        raise E("client not found")
    keep_ids=[
        str(x.get("id")) for x in (c.get("inbound_links") or [])
        if isinstance(x,dict) and x.get("id") and str(x.get("id")) != str((ss or {}).get("id"))
    ]
    safe["before"]={"client":cs,"shadowsocks_present":bool(ss),"target_port":port}

    if ss:
        p.req("DELETE","/inbounds/"+urllib.parse.quote(str(ss.get("id")),safe=""))
        time.sleep(4)

    al=p.get("/allocations")
    available=[int(x) for x in (al.get("available",[]) if isinstance(al,dict) else []) if str(x).isdigit()]
    if port not in available:
        time.sleep(5)
        al=p.get("/allocations")
        available=[int(x) for x in (al.get("available",[]) if isinstance(al,dict) else []) if str(x).isdigit()]
    if port not in available:
        raise E("Port did not become available after removing Shadowsocks")

    create_resp=p.post("/inbounds",{"protocol":"wireguard","port":port})
    private["wireguard_create_response"]=create_resp
    time.sleep(4)

    ib2=inbounds(p.get("/inbounds"))
    wg=find(ib2,"wireguard",None)
    if not wg:
        raise E("WireGuard inbound was not created")

    p.patch("/clients/"+urllib.parse.quote(CLIENT,safe=""),{"wg":True,"inbound_ids":keep_ids})
    time.sleep(5)

    c2,cs2=client_state(p)
    sub=subscription_flags(c2)
    ib3=inbounds(p.get("/inbounds"))
    safe["after"]={
        "client":cs2,
        "subscription":sub,
        "wireguard":{
            "port":wg.get("port"),
            "enabled":wg.get("enabled"),
            "protocol":wg.get("protocol"),
            "network":wg.get("network"),
            "security":wg.get("security")
        }
    }
    safe["results"]=[
        {"protocol":"vless","state":"present" if find(ib3,"vless","gh-vless-reality") else "missing","port":25077},
        {"protocol":"wireguard","state":"present" if find(ib3,"wireguard",None) else "missing","port":port},
        {"protocol":"hysteria2","state":"present" if find(ib3,"hysteria2","gh-hysteria2") else "missing","port":25079}
    ]
    safe["wireguard_inbound_ready"]=bool(wg and wg.get("enabled"))
    safe["wireguard_client_config_ready"]=bool(cs2.get("wg") and cs2.get("wg_conf_present"))
    return safe,private


def main():
    mode=(sys.argv[1] if len(sys.argv)>1 else "inspect").lower()
    if mode not in ("inspect","apply","verify","wgprobe","wgfix","wgtest","restartrepair","replacewg","restorewg"): raise E("bad mode")
    p=Panel(); p.login()
    safe,private={"inspect":inspect,"apply":apply,"verify":verify,"wgprobe":wgprobe,"wgfix":wgfix,"wgtest":wgtest,"restartrepair":restartrepair,"replacewg":replacewg,"restorewg":restorewg}[mode](p)
    save(safe,private)
    print(json.dumps(safe,ensure_ascii=False,indent=2))

try:
    main()
except Exception as e:
    fail={"mode":sys.argv[1] if len(sys.argv)>1 else "inspect","fatal":str(e),"mcp":mcp_check()}
    save(fail,{"fatal":str(e)})
    print(json.dumps(fail,ensure_ascii=False,indent=2))
    raise
