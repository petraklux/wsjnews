"""AI 摘要。无 API Key / 调用失败时自动退化为抽取式摘要,绝不阻塞出稿。"""
from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from config import CFG
from models import Item

SYSTEM = (
    "You are a financial news editor. Given a Wall Street Journal headline and its "
    "official standfirst, write a faithful summary. Never invent facts, numbers, dates, "
    "or quotes absent from the input. If only the headline is available, summarize just "
    "what it states and do not speculate."
)

LANG_HINT = {
    "zh": "用简体中文输出 1-2 句、不超过 80 字的摘要,中立客观,不要任何前缀或标题。",
    "en": "Write a neutral 1-2 sentence summary under 45 words. No prefix, no title.",
}


def fallback(it: Item) -> str:
    text = it.dek or it.title
    return text[:200] + ("…" if len(text) > 200 else "")


@retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=2, max=15), reraise=True)
def _call(client: httpx.Client, prompt: str) -> str:
    r = client.post(
        f"{CFG.base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {CFG.api_key}",
                 "Content-Type": "application/json"},
        json={
            "model": CFG.model,
            "temperature": 0.2,
            "max_tokens": 220,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
        },
    )
    r.raise_for_status()
    return (r.json()["choices"][0]["message"]["content"] or "").strip()


def summarize(items: list[Item]) -> None:
    """就地写入 item.summary。"""
    if not items:
        return
    if not CFG.api_key:
        print("[info] 未配置 OPENAI_API_KEY,使用抽取式摘要")
        for it in items:
            it.summary = fallback(it)
        return

    hint = LANG_HINT.get(CFG.summary_lang, LANG_HINT["zh"])
    ok = failed = 0
    with httpx.Client(timeout=CFG.summary_timeout, proxy=CFG.proxy) as client:
        for i, it in enumerate(items):
            if i >= CFG.summary_max_items:
                it.summary = fallback(it)
                continue
            prompt = (f"{hint}\n\nSection: {it.section}\n"
                      f"Headline: {it.title}\n"
                      f"Standfirst: {it.dek or '(none)'}")
            try:
                it.summary = _call(client, prompt) or fallback(it)
                ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"[warn] 摘要失败 {it.url}: {e!r}")
                it.summary = fallback(it)
                failed += 1
    print(f"[info] 摘要完成: AI {ok} 篇, 降级 {failed} 篇")
