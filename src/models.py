"""数据模型与纯函数工具,不做任何网络请求。"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urljoin, urlparse, urlunparse

from dateutil import parser as dtparser

# 非文章路径前缀
SKIP_PREFIXES = (
    "/news/archive", "/video", "/market-data", "/buyside", "/author",
    "/podcasts", "/newsletters", "/subscribe", "/tools", "/dow-jones",
    "/pro/", "/coupons",
)
NOISE_TITLES = {"read more", "more", "listen", "watch", "subscribe now", "sign in"}


@dataclass
class Item:
    url: str
    title: str
    dek: str = ""
    section: str = ""
    published: str | None = None
    summary: str = ""
    sources: list[str] = field(default_factory=list)

    @property
    def guid(self) -> str:
        return hashlib.sha1(self.url.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["guid"] = self.guid
        return d


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def to_dt(value: str | None) -> dt.datetime | None:
    """宽松解析为 tz-aware UTC datetime。"""
    if not value:
        return None
    try:
        d = dtparser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def iso(value: str | None) -> str | None:
    d = to_dt(value)
    return d.isoformat() if d else None


def plausible_iso(value: str | None) -> str | None:
    """过滤掉明显不合理的时间戳(未来时间 / 1995 年之前)。"""
    d = to_dt(value)
    if not d:
        return None
    now = now_utc()
    if d > now + dt.timedelta(hours=6):
        return None
    if d.year < 1995:
        return None
    return d.isoformat()


def age_hours(value: str | None) -> float | None:
    d = to_dt(value)
    return None if not d else (now_utc() - d).total_seconds() / 3600


def normalize_url(raw: str, base: str = "https://www.wsj.com/") -> str | None:
    """只保留 wsj.com 本域,去掉 query/fragment 与尾斜杠。"""
    if not raw:
        return None
    try:
        u = urlparse(urljoin(base, raw.strip()))
    except ValueError:
        return None
    host = u.netloc.lower().split(":")[0]
    if host not in ("wsj.com", "www.wsj.com"):
        return None
    path = re.sub(r"/+$", "", u.path) or "/"
    return urlunparse(("https", "www.wsj.com", path, "", "", ""))


def looks_like_article(url: str) -> bool:
    path = urlparse(url).path
    if any(path.startswith(p) for p in SKIP_PREFIXES):
        return False
    if path.startswith("/articles/") or path.startswith("/livecoverage/"):
        return True
    segs = [s for s in path.split("/") if s]
    # 形如 /economy/central-banking/some-headline-slug-3f7a1b2c
    return len(segs) >= 2 and "-" in segs[-1] and len(segs[-1]) >= 20


def section_of(url: str) -> str:
    segs = [s for s in urlparse(url).path.split("/") if s]
    if not segs or segs[0] in ("articles", "livecoverage"):
        return "Front Page"
    return segs[0].replace("-", " ").title()


def slug_to_title(url: str) -> str:
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"-[0-9a-f]{6,}$", "", slug)
    return re.sub(r"[-_]+", " ", slug).strip().capitalize()


def clean_text(value: str | None, limit: int = 400) -> str:
    if not value:
        return ""
    return " ".join(value.split())[:limit]


def merge(*groups: list[Item]) -> list[Item]:
    """按 URL 去重合并。先传入的组字段优先(sitemap 的 published/title 最权威)。"""
    merged: dict[str, Item] = {}
    for group in groups:
        for it in group:
            cur = merged.get(it.url)
            if cur is None:
                merged[it.url] = it
                continue
            cur.title = cur.title or it.title
            cur.dek = cur.dek or it.dek
            cur.published = cur.published or it.published
            cur.section = cur.section or it.section
            cur.sources = sorted(set(cur.sources) | set(it.sources))
    return list(merged.values())
