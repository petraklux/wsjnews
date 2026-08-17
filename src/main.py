"""入口。退出码 0 = 成功;1 = 无新鲜内容(不覆盖已有 feed)。"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import feed as feed_builder
from config import CFG
from models import Item, now_iso, to_dt
from net import make_client
from sources import collect, enrich
from summarize import summarize


def load_state() -> dict:
    if os.path.exists(CFG.state_path):
        try:
            with open(CFG.state_path, encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("items", {})
            return data
        except (json.JSONDecodeError, OSError) as e:
            print(f"[warn] state 读取失败,重新开始: {e!r}")
    return {"items": {}, "updated": None}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(CFG.state_path) or ".", exist_ok=True)
    state["updated"] = now_iso()
    with open(CFG.state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def too_old(it: Item) -> bool:
    """条目级过滤。无发布时间的不判定,交由去重逻辑按 first_seen 处理。"""
    d = to_dt(it.published)
    if d is None:
        return False
    return (to_dt(now_iso()) - d).days > CFG.max_item_age_days


def sort_key(rec: dict) -> str:
    return rec.get("published") or rec.get("first_seen") or ""


def main() -> int:
    state = load_state()
    seen: dict[str, dict] = state["items"]
    print(f"[info] 已有 {len(seen)} 条历史记录")

    candidates, report = collect()
    if not candidates:
        print("[error] 所有通道均未取到内容 —— 可能被拦截 / 网络出口异常")
        print("[error] 请运行: PYTHONPATH=src python src/diag.py")
        return 1 if CFG.fail_on_stale else 0

    fresh = [c for c in candidates if not too_old(c)]
    if dropped := len(candidates) - len(fresh):
        print(f"[info] 丢弃 {dropped} 条超过 {CFG.max_item_age_days} 天的旧条目")
    candidates = fresh

    if not candidates:
        print(f"[error] 过滤后无新鲜内容(阈值 {CFG.max_item_age_days} 天)")
        print("[error] 上游疑似停更或被缓存,已跳过发布以保留现有 feed")
        return 1 if CFG.fail_on_stale else 0

    new_items = [c for c in candidates if c.guid not in seen][: CFG.max_new_per_run]
    print(f"[info] 新增 {len(new_items)} 条")

    if new_items:
        with make_client() as client:
            enrich(client, new_items)
        summarize(new_items)
        stamp = now_iso()
        for it in new_items:
            rec = it.to_dict()
            rec["first_seen"] = stamp
            seen[it.guid] = rec

    # 已有条目:补全后来才出现的导语/发布时间
    for c in candidates:
        rec = seen.get(c.guid)
        if rec is None:
            continue
        if c.dek and not rec.get("dek"):
            rec["dek"] = c.dek
        if c.published and not rec.get("published"):
            rec["published"] = c.published

    records = sorted(seen.values(), key=sort_key, reverse=True)[: CFG.keep_items]
    state["items"] = {r["guid"]: r for r in records}

    status = feed_builder.build(records, report)
    save_state(state)

    if CFG.fail_on_stale and status["newest_age_hours"] > CFG.max_channel_age_h * 2:
        print(f"[error] feed 最新条目落后 {status['newest_age_hours']}h,已写出但标记为异常")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
