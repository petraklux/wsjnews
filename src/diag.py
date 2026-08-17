"""诊断:定位陈旧数据/拦截问题。运行: PYTHONPATH=src python src/diag.py"""
from __future__ import annotations

import datetime as dt

from config import CFG
from models import age_hours
from net import ROBOTS, cache_bust, get, make_client
from sources import (DEFAULT_SECTIONS, discover_sitemaps, parse_listing,
                     parse_sitemap)

WATCH = ("date", "age", "expires", "cache-control", "last-modified",
         "x-cache", "cf-cache-status", "x-served-by", "server")
WALL = ("please enable js", "captcha", "access denied", "unusual activity",
        "reference #", "are you a robot")


def probe(client, url: str, label: str):
    print(f"\n{'=' * 72}\n{label}\n{url}")
    try:
        r = get(client, cache_bust(url))
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ 请求失败: {e!r}")
        return None
    print(f"  HTTP {r.status_code} · {len(r.content):,} bytes · 最终 URL: {r.url}")
    for h in WATCH:
        if h in r.headers:
            print(f"    {h}: {r.headers[h]}")
    age = int(r.headers.get("age", "0") or 0)
    if age > 3600:
        print(f"  ⚠️  命中上游缓存: Age = {age / 3600:.1f} 小时")
    if hits := [w for w in WALL if w in r.text[:8000].lower()]:
        print(f"  ⚠️  疑似机器人墙/JS 墙: {hits}")
    return r


def main() -> None:
    print(f"当前 UTC 时间 : {dt.datetime.now(dt.timezone.utc).isoformat()}")
    print(f"PROXY_URL     : {CFG.proxy or '(未设置,直连)'}")
    print(f"RESPECT_ROBOTS: {CFG.respect_robots}")

    verdict: list[tuple[str, float | None]] = []

    with make_client() as client:
        ROBOTS.load(client)
        print(f"robots.txt    : {len(ROBOTS.raw)} 字符, "
              f"公布 {len(ROBOTS.sitemaps())} 个 sitemap")
        for s in ROBOTS.sitemaps():
            print(f"    · {s}")

        # 通道1
        for sm in discover_sitemaps(client):
            r = probe(client, sm, "[通道1] news sitemap")
            tag = f"sitemap:{sm.rstrip('/').split('/')[-1]}"
            if r is None:
                verdict.append((tag, None))
                continue
            children, items = parse_sitemap(r.text)
            stamps = sorted((i.published for i in items if i.published), reverse=True)
            print(f"  子 sitemap {len(children)} 个 · 文章 {len(items)} 篇 · "
                  f"最新 {stamps[0] if stamps else '(无时间戳)'}")
            for it in items[:3]:
                print(f"    · [{it.published}] {it.title[:60]}")
            verdict.append((tag, age_hours(stamps[0]) if stamps else None))

        # 通道2
        r = probe(client, CFG.home_url, "[通道2] 首页 HTML")
        if r is not None:
            items = parse_listing(r.text, "homepage")
            print(f"  解析出 {len(items)} 篇")
            for it in items[:3]:
                print(f"    · {it.title[:60]} | dek={'有' if it.dek else '无'}")
            verdict.append(("homepage", None))

        # 通道3
        for page in DEFAULT_SECTIONS[:3]:
            print(f"\n  robots 允许 {page} ? {ROBOTS.allows(page)}")
            r = probe(client, page, "[通道3] 板块页")
            if r is not None:
                print(f"  解析出 {len(parse_listing(r.text, 'section-page'))} 篇")

        # 正文页 robots 检查
        print(f"\n{'=' * 72}\n正文页 robots 抽查")
        for probe_url in ("https://www.wsj.com/articles/test-slug-12345678",
                          "https://www.wsj.com/finance/some-headline-abcd1234"):
            print(f"  {probe_url} → {'允许' if ROBOTS.allows(probe_url) else '禁止'}")

    print(f"\n{'=' * 72}\n新鲜度汇总（通道阈值 {CFG.max_channel_age_h}h）")
    for name, age in sorted(verdict, key=lambda x: (x[1] is None, x[1] or 0)):
        if age is None:
            print(f"  ？ {name:<34} 无可用时间戳")
        else:
            print(f"  {'✅' if age <= CFG.max_channel_age_h else '💀'} "
                  f"{name:<34} 落后 {age:>8.1f}h ({age / 24:.1f} 天)")


if __name__ == "__main__":
    main()
