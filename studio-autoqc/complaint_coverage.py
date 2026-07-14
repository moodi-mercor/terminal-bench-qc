#!/usr/bin/env python3
"""Check the FULL delivery against every Batch-1 complaint. Deterministic checks report
exact counts; LLM/Modal checks are noted separately. One row per complaint."""
import os,sys,re,ast,json
from collections import Counter
sys.path.insert(0,"skills/static-semantic-qc/scripts")
try: from check_instructions import _est_tokens
except Exception: _est_tokens=lambda s: len(s)//4
D=sys.argv[1]
tasks=[t for t in os.listdir(D) if os.path.isdir(os.path.join(D,t))]
def rd(p):
    try: return open(p,encoding='utf-8',errors='replace').read()
    except: return ""
N=len(tasks)
DBW=['pg_isready','mysqld_safe','redis-server','mongod']
verbose=truth=boot=b64=hashn=vinstall=oinstall=noconf=0
opus=0; avgbad=0; avgmiss=0
reward_nonstd=0; verifier_reads_input=0; baked_truth=0; agent_writable_reward=0; filename_answer=0
scenario_words=Counter()
objectives=Counter(); artifacts=Counter(); cats=Counter()
pathmiss=0
for t in tasks:
    td=os.path.join(D,t)
    instr=rd(f"{td}/instruction.md"); toml=rd(f"{td}/task.toml")
    to=rd(f"{td}/tests/test_outputs.py"); ts=rd(f"{td}/tests/test.sh"); solve=rd(f"{td}/solution/solve.sh")
    # 1 verbose
    if _est_tokens(instr)>1500: verbose+=1
    # 4 naming
    if re.match(r'^task_[0-9a-f]{6,}$',t): hashn+=1
    # 5 model usage
    m=re.search(r'model_tested\s*=\s*"([^"]*)"',toml)
    if m and 'opus' in m.group(1).lower(): opus+=1
    # 6 avg8
    m=re.search(r'avg_at_8\s*=\s*([0-9.]+)',toml)
    if not m: avgmiss+=1
    else:
        v=float(m.group(1))
        if 0<=v<=1 and abs(round(v*8)/8-v)>1e-9: avgbad+=1
    # 9 test delegation
    if re.search(r'\.truth/[^\s"\']*\.py',to) or re.search(r'sys\.path[^\n]*\.truth',to) or re.search(r'import[^\n]*\.truth',to): truth+=1
    # 10 runtime installs
    if any(re.search(r'\b(pip3?|apt(-get)?)\s+install\b',l) and not l.strip().startswith('#') for l in ts.split('\n')): vinstall+=1
    if any(re.search(r'\b(pip3?|apt(-get)?)\s+install\b',l) and not l.strip().startswith('#') and not re.search(r'--no-index|-e |install\s+[./]',l) for l in solve.split('\n')): oinstall+=1
    # 11 encoded content
    if re.search(r'b64decode\(\s*["\'][A-Za-z0-9+/=]{8,}|b64decode\([A-Z_]',to): b64+=1
    # 12 bootstrap
    if sum(1 for w in DBW if w in ts)>=3: boot+=1
    # conftest
    if 'pytest' in ts and '--noconftest' not in ts: noconf+=1
    # group4 sub-buckets (heuristic)
    rp=re.search(r'reward\.txt',ts)
    if 'reward.txt' in ts and not re.search(r'/logs/(tests|verifier)/reward\.txt',ts): reward_nonstd+=1
    if re.search(r'open\([^)]*instruction\.md',to): verifier_reads_input+=1
    # baked truth file exposed: a file named *truth*/*answer*/*expected* under environment/
    envd=f"{td}/environment"
    if os.path.isdir(envd):
        for dp,_,fns in os.walk(envd):
            for fn in fns:
                if re.search(r'truth|answer|expected|solution|\.key$',fn,re.I): baked_truth+=1; break
            else: continue
            break
    # scenario words (cluster keyword check): first noun-ish words of instruction opener
    opener=re.sub(r'[^a-z ]',' ',instr[:120].lower())
    for w in set(opener.split()):
        if len(w)>=5: scenario_words[w]+=1
    # distributions
    for key,ctr in (('task_objective',objectives),('artifact_type',artifacts)):
        blk=re.search(key+r'\s*=\s*\[(.*?)\]',toml,re.S)
        if blk:
            for v in re.findall(r'"([^"]+)"',blk.group(1)): ctr[v]+=1
    c=re.search(r'category\s*=\s*"([^"]*)"',toml)
    if c: cats[c.group(1)]+=1
# cluster keyword: scenario words in >5% of tasks
thresh=0.05*N
hot=[(w,c) for w,c in scenario_words.most_common(40) if c>thresh and w not in
     ('system','using','which','their','after','where','while','there','files','directory','contains','should','under','value','field','records','without','between','across','python','process','output')]
print(f"=== COMPLAINT COVERAGE over {N} tasks ===\n")
rows=[
 ("1 Verbose (>1500 tok)","check_instructions",verbose),
 ("4 Naming (task_hash)","check_structure",hashn),
 ("5 Model=Opus (prefer GPT-5.4)","metadata",opus),
 ("6 avg@8 not k/8","check_metadata",f"{avgbad} (missing {avgmiss})"),
 ("9 Test .truth delegation","check_test_hygiene",truth),
 ("10 Verifier runtime install","check_env_fairness",vinstall),
 ("10 Oracle network install","check_env_fairness",oinstall),
 ("11 Encoded base64 ground truth","check_test_hygiene",b64),
 ("12 Bootstrap 3+ DB","check_test_hygiene",boot),
 ("G4 conftest shadowing","check_leakage",noconf),
 ("G4 reward path nonstandard","check_reward_hack",reward_nonstd),
 ("G4 verifier reads instruction","check_leakage",verifier_reads_input),
 ("G4 baked-truth file exposed","check_reward_hack",baked_truth),
]
for name,chk,cnt in rows:
    print(f"  [{chk:20}] {name:34} : {cnt}")
print(f"\n  [cluster keyword >5%] hot scenario words: {hot if hot else 'none'}")
print(f"\n=== DISTRIBUTIONS ===")
print("  task_objective <10% floor breaches:", [(o,c) for o,c in objectives.items() if c<0.10*N])
print("  artifact_type <5% floor breaches:", [(a,c) for a,c in artifacts.items() if c<0.05*N])
print("  category count:", dict(cats.most_common()))
