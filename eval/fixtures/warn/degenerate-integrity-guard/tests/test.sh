#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
# DEGENERATE: compares the artifact to an in-image baked hash while the agent is
# root — the agent can overwrite both the artifact and /app/.cache/out.sha256.
if sha256sum -c /app/.cache/out.sha256 >/dev/null 2>&1; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
