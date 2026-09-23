#!/usr/bin/env python3
import os, json, base64, urllib.request, subprocess, secrets, tempfile
from datetime import datetime, timedelta, timezone

PUBKEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAlM0Kkz1zGWJOzH0lf1tT
K3VDeo6kYAHUjMg3d3HYYAQdPVRmgrNnDOijWcveuIHot7UgXn+zzpf/eS4xpEb/
nrOOtIO0hgeykhVJqhGeJwZWzv+pUXKVKLAdarQf3ArV+MnW2oBEnG2a9f92d4Ut
JPYaM6YoAExld6BgiCANIK1RChp80QnXNUtDV+wh/vMbL33EM9Npo4lDLzzh5hb7
oOOwNYbE3jvvRMPK+i6pVH9wdBBwDePEzgXg3uvxmYK9wtAIgMEdMXJZaVmRhv1s
p0p6rbN/82eJtmvDW6Ub26lY0zUEsV2+/frrqqqtQuf2iZNjoeHa2MD1oyjFIF7j
ZQIDAQAB
-----END PUBLIC KEY-----
"""

login=os.environ.get("PEK_LOGIN","")
key=os.environ.get("PEK_API_KEY","")
if not login or not key:
    raise SystemExit("PEK credentials missing")

auth=base64.b64encode((login+":"+key).encode()).decode()
base="https://kabinet.pecom.ru/api/v1"

def post(path,payload):
    raw=json.dumps(payload,ensure_ascii=False).encode("utf-8")
    req=urllib.request.Request(base+path,data=raw,method="POST")
    req.add_header("Authorization","Basic "+auth)
    req.add_header("Content-Type","application/json; charset=utf-8")
    with urllib.request.urlopen(req,timeout=45) as r:
        return json.loads(r.read().decode("utf-8"))

def pick(d,*keys):
    if not isinstance(d,dict):
        return None
    for k in keys:
        if k in d:
            return d[k]
    return None

end=datetime.now(timezone.utc).date()
begin=end-timedelta(days=30)
listing=post("/cargos/listallorderbylogin/",{"selectBy":1,"dateBegin":str(begin),"dateEnd":str(end)})
cargos=pick(listing,"cargos","сargos") or []

codes=[]
by_code={}
for c in cargos:
    code=pick(c,"code","сode")
    if code:
        code=str(code)
        codes.append(code)
        by_code[code]=c

status_data={"cargos":[]}
current_data={"cargos":[]}
if codes:
    status_data=post("/cargos/status/",{"cargoCodes":codes[:15]})
    current_data=post("/cargos/currentstatus/",{"cargoCodes":codes[:15]})

status_map={}
for s in pick(status_data,"cargos","сargos") or []:
    cargo=pick(s,"cargo") or {}
    code=pick(cargo,"code","сode")
    if code:
        status_map[str(code)]=s

current_map={}
for s in pick(current_data,"cargos","сargos") or []:
    code=pick(s,"cargoCode","cargoСode","сargoCode")
    if code:
        current_map[str(code)]=s

out=[]
for code in codes:
    l=by_code.get(code,{})
    s=status_map.get(code,{})
    info=pick(s,"info") or {}
    cargo=pick(s,"cargo") or {}
    services=pick(s,"services") or {}
    sender=pick(s,"sender") or {}
    receiver=pick(s,"receiver") or {}
    sb=pick(sender,"branchInfo") or {}
    rb=pick(receiver,"branch") or {}
    receiver_city=rb if isinstance(rb,str) else pick(rb,"city","name")
    current=pick(current_map.get(code,{}),"currentStatus") or {}
    items=pick(services,"items") or []
    paid_total=sum(float(pick(x,"paid") or 0) for x in items if isinstance(x,dict))

    out.append({
        "cargoCode":code,
        "orderNumber":pick(cargo,"orderNumber") or pick(l,"orderNumber"),
        "orderDate":pick(l,"orderDate"),
        "description":pick(cargo,"description") or pick(l,"description"),
        "senderCity":pick(sb,"city") or pick(sender,"branch"),
        "receiverCity":receiver_city,
        "status":pick(info,"cargoStatus"),
        "statusDetail":pick(current,"ClientStatusLevel2","clientStatusLevel2"),
        "statusTooltip":pick(current,"Tooltip","tooltip"),
        "arrivalPlanDateTime":pick(info,"arrivalPlanDateTime"),
        "arrivalContractDateTime":pick(info,"arrivalContractDateTime"),
        "deliveryPlanDate":pick(info,"deliveryPlanDate"),
        "receivedByClientDateTime":pick(info,"receivedByClientDateTime"),
        "amount":pick(cargo,"amount"),
        "weightKg":pick(cargo,"weight"),
        "volumeM3":pick(cargo,"volume"),
        "costRub":pick(services,"sum"),
        "paidRub":paid_total,
        "debtRub":pick(services,"debt")
    })

report={"period":{"dateBegin":str(begin),"dateEnd":str(end)},"count":len(out),"shipments":out}

with tempfile.TemporaryDirectory() as td:
    report_path=os.path.join(td,"report.json")
    enc_path=os.path.join(td,"report.enc")
    pub_path=os.path.join(td,"public.pem")
    keyiv_path=os.path.join(td,"keyiv.txt")
    keyiv_enc_path=os.path.join(td,"keyiv.enc")
    with open(report_path,"w",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,separators=(",",":"))
    with open(pub_path,"w",encoding="utf-8") as f:
        f.write(PUBKEY)
    key_hex=secrets.token_hex(32)
    iv_hex=secrets.token_hex(16)
    with open(keyiv_path,"w",encoding="ascii") as f:
        f.write(key_hex+":"+iv_hex)
    subprocess.run(["openssl","enc","-aes-256-cbc","-K",key_hex,"-iv",iv_hex,"-in",report_path,"-out",enc_path],check=True)
    subprocess.run(["openssl","pkeyutl","-encrypt","-pubin","-inkey",pub_path,
                    "-pkeyopt","rsa_padding_mode:oaep","-pkeyopt","rsa_oaep_md:sha256",
                    "-in",keyiv_path,"-out",keyiv_enc_path],check=True)
    with open(keyiv_enc_path,"rb") as f:
        ek=base64.b64encode(f.read()).decode()
    with open(enc_path,"rb") as f:
        ed=base64.b64encode(f.read()).decode()

print("PEK_ENC_KEYIV="+ek)
print("PEK_ENC_DATA="+ed)
print("PEK_PRIVATE_REPORT=OK")
