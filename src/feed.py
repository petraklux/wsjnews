"""生成 RSS 2.0 / JSON Feed / status.json。"""
from __future__ import annotations

import json
import os

from feedgen.feed import FeedGenerator

from config import CFG
from models import age_hours, now_iso, to_dt

DISCLAIMER = ("自动聚合 wsj.com 公开的标题与官方导语,并附 AI 生成摘要。"
              "全文版权归 Dow Jones & Co. 所有,请点击链接阅读原文。")


def _entry_time(rec: dict):
    return to_dt(rec.get("published") or rec.get("first_seen")) or to_dt(now_iso())


def build(records: list[dict], report: dict | None = None) -> dict:
    """records 需已按时间倒序排好。返回 status 字典。"""
    os.makedirs(CFG.out_dir, exist_ok=True)
    site = CFG.site_url.rstrip("/")

    fg = FeedGenerator()
    fg.id(f"{site}/feed.xml")
    fg.title("WSJ 最新（非官方镜像 · 含 AI 摘要）")
    fg.link(href=f"{site}/feed.xml", rel="self")
    fg.link(href="https://www.wsj.com/", rel="alternate")
    fg.language("zh-CN")
    fg.description(DISCLAIMER)
    fg.ttl(30)
    fg.lastBuildDate(to_dt(now_iso()))

    for rec in records:                      # order='append' 保证输出顺序 = 传入顺序
        fe = fg.add_entry(order="append")
        fe.id(rec["guid"])
        fe.guid(rec["guid"], permalink=False)
        fe.title(rec["title"])
        fe.link(href=rec["url"])
        fe.category(term=rec.get("section") or "News")
        fe.pubDate(_entry_time(rec))
        summary = rec.get("summary") or ""
        dek = rec.get("dek") or ""
        html = f"<p>{summary}</p>"
        if dek and dek[:60] not in summary:
            html += f"<p><i>WSJ 导语：</i>{dek}</p>"
        html += f'<p><a href="{rec["url"]}">阅读原文 →</a></p>'
        fe.description(html)

    fg.rss_file(os.path.join(CFG.out_dir, "feed.xml"), pretty=True)

    json_feed = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": "WSJ 最新（非官方镜像）",
        "description": DISCLAIMER,
        "home_page_url": "https://www.wsj.com/",
        "feed_url": f"{site}/feed.json",
        "items": [{
            "id": r["guid"],
            "url": r["url"],
            "title": r["title"],
            "content_text": r.get("summary") or "",
            "summary": r.get("dek") or "",
            "date_published": r.get("published") or r["first_seen"],
            "tags": [r.get("section") or "News"],
        } for r in records],
    }
    _dump("feed.json", json_feed)

    newest = records[0].get("published") or records[0]["first_seen"] if records else None
    status = {
        "built_at": now_iso(),
        "item_count": len(records),
        "newest_published": newest,
        "newest_age_hours": round(age_hours(newest) or -1, 2),
        "collect_report": report or {},
    }
    _dump("status.json", status)

    print(f"[info] 已写出 {len(records)} 条 → {CFG.out_dir}/feed.xml "
          f"(最新落后 {status['newest_age_hours']}h)")
    return status


def _dump(name: str, payload) -> None:
    with open(os.path.join(CFG.out_dir, name), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
