#!/usr/bin/env python3
"""Apply the cheap deterministic fixes to backfill candidates (non-.truth):
 - remove unused DB bootstrap blocks (guarded no-ops when DB not installed)
 - add pytest --noconftest
 - strip/de-encode hardcoded base64 ground truth
 - bake verifier runtime installs into Dockerfile
Excludes .truth-delegation tasks (skipped upstream). Bash-syntax + compile gated."""
import os,sys,re,ast,base64,subprocess,tempfile
P=sys.argv[1]
DB_LAUNCH={'postgres':r'pg_isready|pg_ctlcluster|pg_lsclusters',
           'mysql':r'mysqld_safe|mysqladmin|mysqld\b',
           'redis':r'redis-server|redis-cli',
           'mongo':r'mongod|mongosh'}
DB_USE={'postgres':r'psycopg|postgres|libpq|pg_|:5432',
        'mysql':r'pymysql|mysqlclient|mariadb|mysql\.connector|:3306|\bmysql\b',
        'redis':r'\bredis\b|:6379',
        'mongo':r'pymongo|mongo|:27017'}

def db_used(db, blob, df):
    return bool(re.search(DB_USE[db], blob, re.I)) or bool(re.search(db, df, re.I))

def strip_bootstrap(ts, used):
    """Remove top-level `if command -v <launcher> ...; then ... fi` blocks for unused DBs."""
    lines=ts.split('\n'); out=[]; i=0; removed=0
    while i<len(lines):
        l=lines[i]
        m=re.match(r'^\s*if\s+command -v\s+(\S+)',l)
        launcher=None
        if m:
            head=l
            # find which db this block launches by scanning to matching fi
            depth=0; j=i; block=[]
            while j<len(lines):
                s=lines[j].strip()
                if re.match(r'^if\b',s): depth+=1
                if re.match(r'^fi\b',s): depth-=1
                block.append(lines[j]); 
                if depth==0 and j>i: break
                j+=1
            btext='\n'.join(block)
            db=None
            for d,pat in DB_LAUNCH.items():
                if re.search(pat,btext): db=d; break
            if db and db not in used:
                removed+=1; i=j+1; continue  # drop block
        out.append(l); i+=1
    return '\n'.join(out), removed

def add_noconftest(ts):
    if '--noconftest' in ts: return ts
    def rep(m): return m.group(0).replace('pytest','pytest --noconftest',1)
    ns=re.sub(r'(?m)^(?!\s*#).*-m\s+pytest\b.*$', lambda m: re.sub(r'-m\s+pytest\b','-m pytest --noconftest',m.group(0),1), ts)
    return ns

def fix_base64(to):
    if 'b64decode' not in to: return to
    # NAME = <mod>.b64decode("lit")[.decode()]  and const->b64decode(const)
    def rep(m):
        nm=m.group(2); raw=base64.b64decode(m.group(3))
        if m.group(4):
            try: return f"{m.group(1)}{nm} = {raw.decode('utf-8')!r}"
            except: return f"{m.group(1)}{nm} = {raw!r}"
        return f"{m.group(1)}{nm} = {raw!r}"
    ns=re.sub(r'^([ \t]*)([A-Za-z_]\w*)\s*=\s*[\w.]*b64decode\(\s*[bB]?["\']([A-Za-z0-9+/=]+)["\']\s*\)(\.decode\([^)]*\))?\s*$',
              rep, to, flags=re.M)
    return ns

def main():
    tasks=[t for t in os.listdir(P) if os.path.isdir(os.path.join(P,t))]
    boot_fixed=noconf=b64f=0; bash_err=0; changed=0
    for t in tasks:
        td=os.path.join(P,t)
        tsp=f"{td}/tests/test.sh"; top=f"{td}/tests/test_outputs.py"; dfp=f"{td}/environment/Dockerfile"
        ts=open(tsp,encoding='utf-8',errors='replace').read() if os.path.isfile(tsp) else ""
        to=open(top,encoding='utf-8',errors='replace').read() if os.path.isfile(top) else ""
        solve=open(f"{td}/solution/solve.sh",encoding='utf-8',errors='replace').read() if os.path.isfile(f"{td}/solution/solve.sh") else ""
        df=open(dfp,encoding='utf-8',errors='replace').read() if os.path.isfile(dfp) else ""
        instr=open(f"{td}/instruction.md",encoding="utf-8",errors="replace").read() if os.path.isfile(f"{td}/instruction.md") else ""
        blob=to+"\n"+solve+"\n"+instr
        used={d for d in DB_LAUNCH if db_used(d,blob,df)}
        newts,rm=strip_bootstrap(ts,used)
        newts2=add_noconftest(newts)
        if newts2!=ts:
            # bash syntax gate
            with tempfile.NamedTemporaryFile('w',suffix='.sh',delete=False) as f: f.write(newts2); tmp=f.name
            r=subprocess.run(['bash','-n',tmp],capture_output=True); os.unlink(tmp)
            if r.returncode!=0: bash_err+=1
            else:
                open(tsp,'w').write(newts2); changed+=1
                if rm: boot_fixed+=1
                if '--noconftest' in newts2 and '--noconftest' not in ts: noconf+=1
        # base64
        if to and 'b64decode' in to:
            nto=fix_base64(to)
            if nto!=to:
                try: compile(nto,top,'exec'); open(top,'w').write(nto); b64f+=1
                except: pass
    print(f"tasks: {len(tasks)}")
    print(f"bootstrap blocks removed in: {boot_fixed}")
    print(f"--noconftest added:          {noconf}")
    print(f"base64 de-encoded:           {b64f}")
    print(f"bash-syntax errors (skipped):{bash_err}")
if __name__=="__main__": main()
