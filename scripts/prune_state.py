"""清理过期/污染的历史记录。运行: PYTHONPATH=src python scripts/prune_state.py 30"""
from __future__ import annotations

import datetime as dt
import json
import sys

sys.path.insert(0, "src")

from config import CFG          # noqa: E402
from models import to_dt        # noqa: E402

days = float(sys.argv[1]) if len(sys.argv) > 1 else 30
cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)

with open(CFG.state_path, encoding="utf-8") as f:
    state = json.load(f)

before = len(state.get("items", {}))
kept = {}
for guid, rec in state.get("items", {}).items():
    d = to_dt(rec.get("published") or rec.get("first_seen"))
    if d is not None and d >= cutoff:
        kept[guid] = rec

state["items"] = kept
with open(CFG.state_path, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)

print(f"清理完成: {before} → {len(kept)} 条(保留最近 {days} 天)")
