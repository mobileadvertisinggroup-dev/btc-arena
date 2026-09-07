#!/usr/bin/env bash
# Full reproduction of the study. Requires YOUTUBE_API_KEY in the environment.
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${YOUTUBE_API_KEY:-}" ]; then
  echo "ERROR: export YOUTUBE_API_KEY=... first (YouTube Data API v3 key)." >&2
  exit 1
fi

echo "=== [1/5] discovery (search.list) ==="   && python3 scripts/discover.py
echo "=== [2/5] verify + select channels ===" && python3 scripts/select_channels.py
echo "=== [3/5] collect videos ==="            && python3 scripts/collect_selected.py
echo "=== [4/5] classify ==="                  && python3 scripts/classify.py
echo "=== [5/5] analyze ==="                   && python3 scripts/analyze.py
echo "=== quota used today ==="
python3 - <<'PY'
import json, datetime as dt, pathlib
p = pathlib.Path("logs/quota_ledger.jsonl")
today = dt.datetime.now(dt.timezone.utc).date().isoformat()
tot = sum(json.loads(l).get("cost",0) for l in p.read_text().splitlines()
          if l.strip() and json.loads(l).get("ts","").startswith(today)) if p.exists() else 0
print(f"{tot} / 10000 units")
PY
