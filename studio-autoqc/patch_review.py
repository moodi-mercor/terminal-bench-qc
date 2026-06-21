import json, requests
API="https://api.studio.mercor.com";CAMP="camp_4e196b1414a1499db54b43233104b0a7";COMP="comp_2fa4115109d741cd94a3c409ed89e61f";ACCT="acct_85b680d4c5ba49a29f19c173672aebea";SID="qcspec_7bddfd703a12994dbc31fd1b";ROOT="/Users/mahmoodmapara/Desktop/terminal-bench-qc"
def key():
  for l in open(f"{ROOT}/.env"):
    if l.startswith("RLS_KEY="): return l.split("=",1)[1].strip().strip('"').strip("'")
H={"Authorization":"Bearer "+key(),"X-Campaign-Id":CAMP,"X-Company-Id":COMP,"X-Account-Id":ACCT,"User-Agent":"curl/8.7.1","Content-Type":"application/json"}
spec=json.load(open(f"{ROOT}/_local/tb_modules/01_task_quality_review_v2.json"))
cur=requests.get(f"{API}/qc-specs/{SID}",headers=H,timeout=60).json()
json.dump(cur,open(f"{ROOT}/_local/tb_modules/_snapshot_review_v{cur.get('version')}.json","w"),indent=2)
print("snapshot review v"+str(cur.get('version')))
r=requests.patch(f"{API}/qc-specs/{SID}",headers=H,data=json.dumps({"spec":spec,"name":"Task Quality Review","description":spec["rubric"]["description"][:480]}),timeout=120)
print("PATCH ->",r.status_code,"new version:",r.json().get("version") if r.status_code<300 else r.text[:300])
