import sys
# In-image grader: re-reads the output and prints PASS/FAIL.
total = int(open("/app/out/total.txt").read().strip())
print("PASS" if total == 32 else "FAIL")
