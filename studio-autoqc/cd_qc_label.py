#!/usr/bin/env python3
"""Write QC status + provenance to the net-new client-delivery tasks in RLS."""
import csv, json, os, time, requests
API="https://api.studio.mercor.com"
CAMP="camp_4e196b1414a1499db54b43233104b0a7";COMP="comp_2fa4115109d741cd94a3c409ed89e61f";ACCT="acct_85b680d4c5ba49a29f19c173672aebea"
K=[l.split("=",1)[1].strip().strip('"').strip("'") for l in open(".env") if l.startswith("RLS_KEY=")][0]
HJ={"Authorization":f"Bearer {K}","X-Campaign-Id":CAMP,"X-Company-Id":COMP,"X-Account-Id":ACCT,"User-Agent":"curl/8.7.1","Content-Type":"application/json"}
CD="/private/tmp/claude-501/-Users-mahmoodmapara-Desktop-terminal-bench-qc/1f204211-5ba8-469d-bfaa-7ae458192941/scratchpad/client_del"
namemap=json.load(open(f"{CD}/qc_namemap.json")); verdict=json.load(open(f"{CD}/qc_verdict.json"))
netnew={it["hash"]:it for it in json.load(open(f"{CD}/netnew.json"))}
orc={}
for l in open("_local/client_del_qc/oracle_out.txt"):
    a=l.rstrip("\n").split("\t"); orc[a[0]]=a[1] if len(a)>=2 else ""
leak=set()
for l in open("_local/client_del_qc/leak_out.tsv"):
    a=l.rstrip("\n").split("\t")
    if len(a)>=3 and ((a[2].isdigit() and int(a[2])>0) or a[1]=="PASS"): leak.add(a[0])
def toml_meta(d):
    p=os.path.join(d,"task.toml"); m={}; inm=False
    if not os.path.exists(p): return m
    for line in open(p,encoding="utf-8",errors="replace"):
        s=line.strip()
        if s.startswith("["): inm=s=="[metadata]"; continue
        if inm and "=" in s: k,v=s.split("=",1); m[k.strip()]=v.strip().strip('"')
    return m
rows=[]
for name,v in namemap.items():
    tid=v["task_id"]; h=v["hash"]
    if not tid: continue
    vd=verdict.get(name,"FAIL"); o=orc.get(name,"")
    reason = ("leak" if name in leak else o) if vd=="FAIL" else ""
    it=netnew.get(h,{}); prov=it.get("prov",[{}])
    clients=",".join(sorted({p.get("client","") for p in prov}))
    repos=",".join(sorted({p.get("repo","") for p in prov}))
    paths=" ; ".join(sorted({p.get("repo","")+"/"+p.get("path","") for p in prov})[:8])
    m=toml_meta(it.get("rep_dir","")) if it else {}
    f={"delivered":"yes","delivered_to":clients,"delivered_repo":repos,"delivered_path":paths,
       "source":"client-delivery-import","harness":"terminal-bench",
       "qc_status": "pass" if vd=="PASS" else "fail", "qc_fail_reason":reason,
       "qc_gate":"oracle+leak+static","difficulty":m.get("difficulty") or "","domain":m.get("category") or "","subcategory":m.get("subcategory") or ""}
    upl=[{"column_key":f"custom_fields->>'{k}'","value":val} for k,val in f.items() if val]
    rows.append({"task_id":tid,"updates":upl})
print(f"labeling {len(rows)} net-new with qc_status + provenance")
for i in range(0,len(rows),500):
    for att in range(4):
        r=requests.post(f"{API}/tasks/bulk-update",headers=HJ,json={"updates":rows[i:i+500]},timeout=300)
        if r.status_code==200 and all(x.get("success") for x in r.json().get("results",[])): break
        if r.status_code==429: time.sleep(15*(att+1)); continue
        print("  partial",r.status_code,r.text[:100]); break
    print(f"  [{min(i+500,len(rows))}/{len(rows)}]",flush=True); time.sleep(2)
print("DONE")
