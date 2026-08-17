"""HTTP 层:统一客户端、重试、缓存穿透、robots.txt。"""
from __future__ import annotations

import time
import urllib.robotparser as robotparser

import httpx
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential_jitter)

from config import CFG

ROBOTS_URL = "https://www.wsj.com/robots.txt"


def build_headers() -> dict[str, str]:
    return {
        "User-Agent": CFG.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        # 关键:防止中间层/CDN 返回陈年副本
        "Cache-Control": "no-cache, no-store, max-age=0",
        "Pragma": "no-cache",
    }


def make_client() -> httpx.Client:
    return httpx.Client(
        headers=build_headers(),
        timeout=CFG.timeout,
        follow_redirects=True,
        http2=True,
        proxy=CFG.proxy,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=2, max=20),
    retry=retry_if_exception_type((httpx.HTTPError,)),
    reraise=True,
)
def get(client: httpx.Client, url: str) -> httpx.Response:
    r = client.get(url)
    r.raise_for_status()
    return r


def get_text(client: httpx.Client, url: str) -> str:
    return get(client, url).text


def cache_bust(url: str) -> str:
    """加一次性查询参数,绕开中间层缓存。"""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}_cb={int(time.time())}"


class Robots:
    """用我们自己的客户端拉 robots.txt(避免 urllib 被 403),再交给标准解析器。"""

    def __init__(self) -> None:
        self._rp: robotparser.RobotFileParser | None = None
        self._loaded = False
        self.raw: str = ""

    def load(self, client: httpx.Client) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            self.raw = get_text(client, ROBOTS_URL)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] robots.txt 拉取失败,跳过合规检查: {e!r}")
            return
        rp = robotparser.RobotFileParser()
        rp.parse(self.raw.splitlines())
        self._rp = rp

    def allows(self, url: str) -> bool:
        if not CFG.respect_robots or self._rp is None:
            return True
        try:
            return self._rp.can_fetch(CFG.robots_ua, url)
        except Exception:  # noqa: BLE001
            return True

    def sitemaps(self) -> list[str]:
        import re
        return re.findall(r"(?im)^\s*sitemap:\s*(\S+)", self.raw)


ROBOTS = Robots()
