"""OpenAI client shared by the agent, voice and helper services (key: OPENAI_API_KEY)."""
import os
from dataclasses import dataclass, field
from typing import Optional

from openai import AsyncOpenAI

from services import timing

_client: Optional[AsyncOpenAI] = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:  # lazy: importing services (tests, tools) must not need the key
        # SDK defaults are 600 s and 2 retries; a driver can't wait that long for any single call.
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=30.0, max_retries=1)
    return _client


@dataclass
class LlmResult:
    text: str
    usage: dict = field(default_factory=dict)  # prompt_tokens, completion_tokens, cached_tokens (0 when unknown)


def _usage(res) -> dict:
    u = getattr(res, "usage", None)
    if u is None:
        return {}
    details = getattr(u, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    return {"prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
            "cached_tokens": int(cached or 0)}


async def complete_full(system: str, text: str, model: str = "gpt-5.4-mini", image_b64: Optional[str] = None,
                        cache_key: Optional[str] = None) -> LlmResult:
    """One system + user turn with token usage. `cache_key` routes requests that share a prefix to the
    same prompt cache (OpenAI prompt_cache_key). Token counts also land in the request timings as n_*."""
    content: str | list = text
    if image_b64:
        content = [{"type": "text", "text": text},
                   {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]
    extra = {"prompt_cache_key": cache_key} if cache_key else {}
    res = await client().chat.completions.create(
        model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
        extra_body=extra or None)
    usage = _usage(res)
    for k in ("prompt_tokens", "completion_tokens", "cached_tokens"):
        if usage.get(k):
            timing.add(f"n_{k}", usage[k])
    return LlmResult(text=res.choices[0].message.content or "", usage=usage)


async def complete(system: str, text: str, model: str = "gpt-5.4-mini", image_b64: Optional[str] = None) -> str:
    """One system + user turn; returns the reply text."""
    return (await complete_full(system, text, model, image_b64)).text
