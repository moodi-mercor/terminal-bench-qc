#!/usr/bin/env python3
"""Stream a (multi-GB) trajectory_batch_export.json and emit a compact per-task
JSONL with the reward.txt scores + custom_fields (diversity dims), dropping the
heavy trajectory_messages.

Usage:
  python extract_export.py export_old.json compact_old.jsonl
"""
import ijson
import json
import sys
from decimal import Decimal

PATH = "worlds.item.orchestrators.item.tasks.item"


def _enc(o):
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(repr(o))


def main(src, dst):
    n = 0
    with open(src, "rb") as f, open(dst, "w") as out:
        for t in ijson.items(f, PATH):
            trajs = t.get("trajectories", []) or []
            scores, statuses = [], []
            for tr in trajs:
                to = tr.get("trajectory_output") or {}
                statuses.append(tr.get("trajectory_status"))
                sc = to.get("score")
                if sc is not None:
                    scores.append(sc)
            rec = {
                "task_name": t.get("task_name"),
                "task_id": t.get("task_id"),
                "custom_fields": t.get("custom_fields") or {},
                "n_traj": len(trajs),
                "scores": scores,
                "statuses": statuses,
            }
            out.write(json.dumps(rec, default=_enc) + "\n")
            n += 1
            if n % 500 == 0:
                print(f"  {n} tasks...", flush=True)
    print(f"extracted {n} tasks -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
