import sys
n=int(open("/app/out/warn.txt").read().strip())
print("PASS" if n==2 else "FAIL")
