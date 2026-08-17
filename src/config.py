"""全部可调参数集中在此,只从环境变量读取。"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    return str(os.getenv(name, str(default))).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    # ---- 站点 ----
    home_url: str = os.getenv("WSJ_HOME", "https://www.wsj.com/")
    site_url: str = os.getenv("SITE_URL", "https://example.github.io/wsj-rss")

    # ---- 网络 ----
    timeout: float = _float("TIMEOUT", 25)
    request_delay: float = _float("REQUEST_DELAY", 1.2)
    proxy: str | None = os.getenv("PROXY_URL") or None
    user_agent: str = os.getenv("USER_AGENT", (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ))

    # ---- 合规 ----
    respect_robots: bool = _bool("RESPECT_ROBOTS", True)
    robots_ua: str = os.getenv("ROBOTS_UA", "*")

    # ---- 通道1: news sitemap ----
    sitemap_max_files: int = _int("SITEMAP_MAX_FILES", 6)
    sitemap_max_child: int = _int("SITEMAP_MAX_CHILD", 3)
    sitemap_max_items: int = _int("SITEMAP_MAX_ITEMS", 120)

    # ---- 通道3: 板块页(逗号分隔,留空用内置默认) ----
    section_pages: str = os.getenv("SECTION_PAGES", "")

    # ---- 元数据补全(回访文章页取 og:description) ----
    fetch_meta: bool = _bool("FETCH_META", True)
    meta_limit: int = _int("META_LIMIT", 40)

    # ---- 新鲜度闸门 ----
    max_channel_age_h: float = _float("MAX_CHANNEL_AGE_H", 12)
    max_item_age_days: float = _float("MAX_ITEM_AGE_DAYS", 14)
    fail_on_stale: bool = _bool("FAIL_ON_STALE", True)

    # ---- 摘要(OpenAI 兼容接口) ----
    api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model: str = os.getenv("SUMMARY_MODEL", "gpt-4o-mini")
    summary_lang: str = os.getenv("SUMMARY_LANG", "zh")
    summary_max_items: int = _int("SUMMARY_MAX_ITEMS", 25)
    summary_timeout: float = _float("SUMMARY_TIMEOUT", 60)

    # ---- 输出 ----
    max_new_per_run: int = _int("MAX_NEW_PER_RUN", 40)
    keep_items: int = _int("KEEP_ITEMS", 200)
    out_dir: str = os.getenv("OUT_DIR", "public")
    state_path: str = os.getenv("STATE_PATH", "state/seen.json")


CFG = Config()
