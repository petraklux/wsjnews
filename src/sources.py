"""三通道采集:news sitemap(主力)+ 首页 HTML + 板块页 HTML。"""
from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from lxml import etree

from config import CFG
from models import (NOISE_TITLES, Item, age_hours, clean_text, looks_like_article,
                    merge, normalize_url, plausible_iso, section_of, slug_to_title)
from net import ROBOTS, cache_bust, get_text, make_client

# robots.txt 未公布 news sitemap 时的兜底猜测
SITEMAP_FALLBACK = (
    "https://www.wsj.com/sitemap-news.xml",
    "https://www.wsj.com/wsjsitemaps/wsj_google_news.xml",
)

DEFAULT_SECTIONS = (
    "https://www.wsj.com/news/markets",
    "https://www.wsj.com/news/business",
    "https://www.wsj.com/news/world",
    "https://www.wsj.com/news/technology",
    "https://www.wsj.com/news/economy",
    "https://www.wsj.com/news/politics",
)


# ============ 新鲜度闸门 ============

def newest_age_hours(items: list[Item]) -> float | None:
    stamps = sorted((i.published for i in items if i.published), reverse=True)
    return age_hours(stamps[0]) if stamps else None


def gate(name: str, items: list[Item]) -> list[Item]:
    """通道级判定:整条通道过期就整体丢弃,避免陈货污染 feed。"""
    if not items:
        print(f"[warn] 通道 {name}: 0 篇")
        return []
    age = newest_age_hours(items)
    if age is None:
        print(f"[warn] 通道 {name}: {len(items)} 篇,无时间戳,放行待条目级过滤")
        return items
    if age > CFG.max_channel_age_h:
        print(f"[STALE] 通道 {name}: 最新条目落后 {age:.1f}h ({age / 24:.1f} 天) "
              f"> 阈值 {CFG.max_channel_age_h}h,整体丢弃")
        return []
    print(f"[ok] 通道 {name}: {len(items)} 篇,最新落后 {age:.1f}h")
    return items


# ============ 通道1: news sitemap ============

def _parse_xml(text: str):
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    try:
        return etree.fromstring(text.encode("utf-8", "replace"), parser=parser)
    except etree.XMLSyntaxError as e:
        print(f"[warn] XML 解析失败: {e!r}")
        return None


def _els(node, local_name: str):
    """按 local-name 查找,彻底规避命名空间差异。"""
    return node.xpath(f".//*[local-name()=$n]", n=local_name)


def _text(node, local_name: str, ns_hint: str = "") -> str:
    """取文本;ns_hint 用于在同名标签(如 news:title vs image:title)间择优。"""
    found = _els(node, local_name)
    if not found:
        return ""
    if ns_hint:
        for el in found:
            if ns_hint in str(el.tag):
                return (el.text or "").strip()
    return (found[0].text or "").strip()


def parse_sitemap(text: str) -> tuple[list[str], list[Item]]:
    """返回 (子 sitemap URL 列表, 文章条目列表)。"""
    root = _parse_xml(text)
    if root is None:
        return [], []

    children: list[str] = []
    for sm in _els(root, "sitemap"):
        loc = _text(sm, "loc")
        if loc:
            children.append(loc)

    items: list[Item] = []
    for node in _els(root, "url"):
        url = normalize_url(_text(node, "loc"))
        if not url or not looks_like_article(url):
            continue
        title = clean_text(_text(node, "title", ns_hint="sitemap-news"), 300)
        stamp = (_text(node, "publication_date", ns_hint="sitemap-news")
                 or _text(node, "lastmod"))
        items.append(Item(
            url=url,
            title=title or slug_to_title(url),
            section=section_of(url),
            published=plausible_iso(stamp),
            sources=["sitemap-news"],
        ))
    return children, items


def discover_sitemaps(client: httpx.Client) -> list[str]:
    """从 robots.txt 自动发现 sitemap,优先含 'news' 的;不写死 URL。"""
    ROBOTS.load(client)
    published = ROBOTS.sitemaps()
    if published:
        print(f"[info] robots.txt 公布 {len(published)} 个 sitemap")
    else:
        print("[warn] robots.txt 未公布 sitemap,使用兜底候选")

    ranked = ([u for u in published if "news" in u.lower()]
              + [u for u in published if "news" not in u.lower()])
    for u in SITEMAP_FALLBACK:
        if u not in ranked:
            ranked.append(u)
    return ranked[: CFG.sitemap_max_files]


def from_news_sitemap(client: httpx.Client) -> list[Item]:
    collected: list[Item] = []
    queue: list[tuple[str, int]] = [(u, 0) for u in discover_sitemaps(client)]
    visited: set[str] = set()

    while queue:
        url, depth = queue.pop(0)
        if url in visited or depth > 1:
            continue
        visited.add(url)
        try:
            children, items = parse_sitemap(get_text(client, cache_bust(url)))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] sitemap {url} 失败: {e!r}")
            continue

        if items:
            print(f"[info] sitemap {url} → {len(items)} 篇")
            collected += items
        elif children and depth == 0:
            take = children[: CFG.sitemap_max_child]
            print(f"[info] {url} 是 sitemapindex,下钻 {len(take)}/{len(children)} 个子文件")
            queue += [(c, depth + 1) for c in take]
        time.sleep(0.5)

    collected.sort(key=lambda i: i.published or "", reverse=True)
    return gate("sitemap-news", collected[: CFG.sitemap_max_items])


# ============ 通道2/3: 首页与板块页 HTML ============

def parse_listing(html: str, source: str) -> list[Item]:
    soup = BeautifulSoup(html, "lxml")
    found: dict[str, Item] = {}

    # a) JSON-LD ItemList(结构化,优先)
    import json
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for block in (data if isinstance(data, list) else [data]):
            if not isinstance(block, dict):
                continue
            for el in block.get("itemListElement") or []:
                if not isinstance(el, dict):
                    continue
                inner = el.get("item") if isinstance(el.get("item"), dict) else {}
                url = normalize_url(el.get("url") or inner.get("url") or "")
                name = clean_text(el.get("name") or inner.get("headline"), 300)
                if url and looks_like_article(url):
                    found.setdefault(url, Item(url=url, title=name, sources=[source]))

    # b) 兜底:遍历页面链接(不依赖易变的 class 名)
    for a in soup.select("a[href]"):
        url = normalize_url(a.get("href", ""))
        if not url or not looks_like_article(url):
            continue
        title = clean_text(a.get_text(" ", strip=True), 300)
        if title.lower() in NOISE_TITLES or len(title) < 15:
            title = ""
        dek = ""
        container = a.find_parent(["article", "div", "li"])
        if container is not None:
            p = container.find("p")
            if p is not None:
                dek = clean_text(p.get_text(" ", strip=True))
        it = found.get(url)
        if it is not None:
            it.title = it.title or title
            it.dek = it.dek or dek
        elif title:
            found[url] = Item(url=url, title=title, dek=dek, sources=[source])

    for it in found.values():
        it.section = it.section or section_of(it.url)
    return [i for i in found.values() if i.title]


def from_homepage(client: httpx.Client) -> list[Item]:
    if not ROBOTS.allows(CFG.home_url):
        print(f"[skip] robots.txt 不允许抓取首页 {CFG.home_url}")
        return []
    try:
        items = parse_listing(get_text(client, cache_bust(CFG.home_url)), "homepage")
        print(f"[info] 首页 → {len(items)} 篇")
        return items
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 首页抓取失败: {e!r}")
        return []


def from_sections(client: httpx.Client) -> list[Item]:
    pages = [p.strip() for p in CFG.section_pages.split(",") if p.strip()] or list(DEFAULT_SECTIONS)
    out: list[Item] = []
    for page in pages:
        if not ROBOTS.allows(page):
            print(f"[skip] robots.txt 不允许抓取 {page}")
            continue
        try:
            items = parse_listing(get_text(client, cache_bust(page)), "section-page")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 板块页 {page} 失败: {e!r}")
            continue
        print(f"[info] 板块页 {urlparse(page).path} → {len(items)} 篇")
        out += items
        time.sleep(CFG.request_delay)
    return out


# ============ 元数据补全 ============

def enrich(client: httpx.Client, items: list[Item]) -> None:
    """回访文章页取 og:description / 发布时间。失败或被 robots 禁止时静默跳过。"""
    if not CFG.fetch_meta:
        return
    todo = [i for i in items if not i.dek or not i.published][: CFG.meta_limit]
    if not todo:
        return
    print(f"[info] 补全元数据: {len(todo)} 篇")
    blocked = 0
    for it in todo:
        if not ROBOTS.allows(it.url):
            blocked += 1
            continue
        try:
            soup = BeautifulSoup(get_text(client, it.url), "lxml")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 元数据 {it.url}: {e!r}")
            continue

        def meta(*selectors: str) -> str:
            for s in selectors:
                tag = soup.select_one(s)
                if tag is not None and tag.get("content"):
                    return tag["content"].strip()
            return ""

        it.dek = it.dek or clean_text(
            meta('meta[property="og:description"]', 'meta[name="description"]'))
        it.title = it.title or clean_text(meta('meta[property="og:title"]'), 300)
        it.published = it.published or plausible_iso(
            meta('meta[property="article:published_time"]',
                 'meta[itemprop="datePublished"]',
                 'meta[name="dateCreated"]'))
        time.sleep(CFG.request_delay)
    if blocked:
        print(f"[info] robots.txt 禁止回访 {blocked} 篇正文页(将仅用标题生成摘要)")


# ============ 汇总入口 ============

def collect() -> tuple[list[Item], dict]:
    """返回 (合并后的条目, 采集报告)。"""
    with make_client() as client:
        ROBOTS.load(client)
        sitemap_items = from_news_sitemap(client)
        home_items = from_homepage(client)
        section_items = from_sections(client)

    # sitemap 放最前:其 published/title 最权威,merge 时优先保留
    items = merge(sitemap_items, home_items, section_items)

    dist: dict[str, int] = {}
    for it in items:
        for s in it.sources:
            dist[s] = dist.get(s, 0) + 1
    report = {
        "sitemap": len(sitemap_items),
        "homepage": len(home_items),
        "sections": len(section_items),
        "merged": len(items),
        "by_source": dist,
    }
    print(f"[info] 合并后 {len(items)} 篇,来源分布: {dist}")
    return items, report
