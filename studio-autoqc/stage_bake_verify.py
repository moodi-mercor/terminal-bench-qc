#!/usr/bin/env python3
"""Stage inlined candidates into a Modal-verify workspace, baking any third-party deps the
inlined verifier imports into the task Dockerfile (since the install was stripped from the
verifier). Writes <W>/tasks/<t> with candidate test_outputs.py + dep-baked Dockerfile.
"""
import os,re,sys,shutil,glob

# import name -> pip package (when they differ)
PIP={"yaml":"pyyaml","cv2":"opencv-python-headless","PIL":"pillow","sklearn":"scikit-learn",
     "bs4":"beautifulsoup4","dateutil":"python-dateutil","OpenSSL":"pyopenssl","cbor2":"cbor2"}
THIRD={"numpy","pandas","yaml","scipy","requests","cryptography","lxml","pyarrow","msgpack",
       "zstandard","brotli","cbor2","dateutil","sklearn","cv2","PIL","bs4","networkx","sympy",
       "jsonschema","protobuf","google","matplotlib","numba","numexpr","tabulate","openpyxl"}

def third_party_imports(code):
    mods=set()
    for m in re.finditer(r'^\s*(?:import|from)\s+([a-zA-Z0-9_]+)', code, re.M):
        if m.group(1) in THIRD: mods.add(m.group(1))
    return {PIP.get(x,x) for x in mods}

def bake(dockerfile_src, pkgs):
    have=dockerfile_src
    need=[p for p in sorted(pkgs) if re.search(r'\b'+re.escape(p)+r'\b', have) is None]
    if not need: return dockerfile_src, []
    lines=have.split('\n'); ins=None
    for i,l in enumerate(lines):
        if re.search(r'ENV PATH=.*venv', l): ins=i+1
    if ins is None:
        for i,l in enumerate(lines):
            if l.strip().startswith('RUN'): ins=i+1
    run=f"RUN pip install --no-cache-dir {' '.join(need)} || pip install --break-system-packages --no-cache-dir {' '.join(need)}"
    if ins is None: return have.rstrip()+"\n"+run+"\n", need
    lines[ins:ins]=[run]; return '\n'.join(lines), need

def main():
    D, CAND, W = sys.argv[1:4]
    os.makedirs(os.path.join(W,"tasks"), exist_ok=True)
    cands=[f[:-3] for f in os.listdir(CAND) if f.endswith('.py')]
    staged=0; baked=0
    for t in cands:
        code=open(os.path.join(CAND,t+".py"),encoding='utf-8',errors='replace').read()
        dst=os.path.join(W,"tasks",t)
        if os.path.exists(dst): shutil.rmtree(dst)
        shutil.copytree(os.path.join(D,t), dst)
        open(os.path.join(dst,"tests","test_outputs.py"),'w').write(code)
        pkgs=third_party_imports(code)
        if pkgs:
            dfp=os.path.join(dst,"environment","Dockerfile")
            new,need=bake(open(dfp,encoding='utf-8',errors='replace').read(), pkgs)
            if need: open(dfp,'w').write(new); baked+=1
        staged+=1
    open(os.path.join(W,"tasks.txt"),'w').write("\n".join(cands))
    print(f"staged {staged}, dep-baked Dockerfiles {baked}")

if __name__=="__main__": main()
