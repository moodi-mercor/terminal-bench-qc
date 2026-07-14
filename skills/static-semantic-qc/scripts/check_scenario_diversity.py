#!/usr/bin/env python3
"""Dataset-level scenario-word diversity check (Reflection clusters concern).

Client feedback: clusters of tasks share the same premise/opening ("An air-gapped kiosk
maintains a durable ...", microgrid, berth, ...). Committed CA: flag any scenario word that
appears in more than ~5% of tasks. This extracts salient scenario nouns from each instruction
(esp. the opening premise) and reports the task-frequency of each; any word over the threshold
is an over-concentrated cluster to reframe.

Usage: python check_scenario_diversity.py <tasks-dir> [--threshold 0.05] [--out clusters.json]
"""
import argparse, json, os, re, collections

# generic terminal/engineering vocabulary that is NOT a distinguishing scenario word
STOP = set("""the a an and or of to in for on with by from as is are be at it this that these those
into over under per via using use used must should will shall each any all no not you your our we
task tool file files path paths dir directory output input data value values run running runs record
records system service must-not create creates created produce read write writes reads generate
report reports validate verify check checks test tests script scripts implement build builds command
line lines format json csv text binary bytes byte field fields entry entries key keys id name names
number count total time timestamp date error errors fail fails pass passes result results state states
given when then based per set get list new old first last next given process processing store stored
schema config configuration option options mode flag flags version versions given contains contain
directory app tmp var log logs bin src environment container docker image build
every need already across lives complete before during after robust named public local
installed while where which whose there their they them then than have has had was were been
being does did doing done make makes made only just also more most some many few such other
another same different various several between within without about above below only ensure
ensures ensuring provide provides provided require requires required contain contains write reads
already-installed pre-installed maintain maintains maintaining operate operates operating
handle handles handling manage manages managing support supports supported allow allows
current currently existing latest single multiple various given exactly correctly properly""".split())

# extra domain-generic words to ignore (too broad to be a "scenario")
GENERIC = set("pipeline audit ledger cache journal engine tool toolkit toolchain manager handler "
              "processor generator validator verifier resolver reconciler compiler indexer builder "
              "service replay collation reconciliation alignment recovery integrity".split())

WORD = re.compile(r"[a-z][a-z0-9\-]{3,}")


def scenario_words(text, opening_chars=400):
    """Salient scenario nouns: content words from the opening premise, minus stop/generic."""
    head = text[:opening_chars].lower()
    words = [w for w in WORD.findall(head) if w not in STOP and w not in GENERIC]
    return set(words)


def check(tasks_dir, threshold):
    tasks = [t for t in os.listdir(tasks_dir) if os.path.isdir(os.path.join(tasks_dir, t))]
    n = len(tasks)
    freq = collections.Counter()
    per_word_tasks = collections.defaultdict(list)
    for t in tasks:
        p = os.path.join(tasks_dir, t, "instruction.md")
        if not os.path.isfile(p):
            continue
        txt = open(p, encoding="utf-8", errors="replace").read()
        for w in scenario_words(txt):
            freq[w] += 1
            per_word_tasks[w].append(t)
    over = [(w, c, c / n) for w, c in freq.most_common() if c / n > threshold]
    return n, over, per_word_tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--threshold", type=float, default=0.05)
    ap.add_argument("--out", default="scenario_clusters.json")
    args = ap.parse_args()
    n, over, per = check(args.tasks, args.threshold)
    print(f"[scenario-diversity] {n} tasks; scenario words over {args.threshold*100:.0f}%: {len(over)}")
    for w, c, frac in over:
        print(f"  {w:22s} {c:5d}  ({frac*100:.1f}%)")
    json.dump({"tasks": n, "threshold": args.threshold,
               "over_threshold": [{"word": w, "count": c, "frac": round(f, 4),
                                   "tasks": per[w]} for w, c, f in over]},
              open(args.out, "w"))
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
