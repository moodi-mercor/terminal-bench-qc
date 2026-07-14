#!/usr/bin/env python3
"""Exact Reflection diversity check: pairwise cosine < 0.90 via all-MiniLM-L6-v2
across instruction.md, solve.sh, test_outputs.py. Reports violating pairs per stream."""
import os,sys,json
import numpy as np
from sentence_transformers import SentenceTransformer
D=sys.argv[1]
tasks=sorted(t for t in os.listdir(D) if os.path.isdir(os.path.join(D,t)))
def rd(p):
    try: return open(p,encoding='utf-8',errors='replace').read()
    except: return ""
m=SentenceTransformer('all-MiniLM-L6-v2')
streams={'instruction.md':'instruction.md','solve.sh':'solution/solve.sh','test_outputs.py':'tests/test_outputs.py'}
report={}
for label,rel in streams.items():
    texts=[rd(os.path.join(D,t,rel))[:8000] for t in tasks]
    emb=m.encode(texts, batch_size=128, show_progress_bar=False, normalize_embeddings=True)
    emb=np.asarray(emb,dtype=np.float32)
    viol=0; pairs=[]
    # chunked cosine to find pairs >=0.90 (upper triangle)
    B=512
    for i in range(0,len(tasks),B):
        sim=emb[i:i+B]@emb.T  # (B, N)
        for r in range(sim.shape[0]):
            gi=i+r
            js=np.where(sim[r]>=0.90)[0]
            js=js[js>gi]
            for j in js:
                viol+=1
                if len(pairs)<40: pairs.append((tasks[gi],tasks[j],round(float(sim[r][j]),3)))
    report[label]={'violating_pairs':viol,'sample':pairs[:20]}
    print(f"{label}: {viol} pairs with cosine>=0.90")
json.dump(report,open("_local/qc_out_delivery/minilm_report.json","w"),indent=1)
print("saved _local/qc_out_delivery/minilm_report.json")
