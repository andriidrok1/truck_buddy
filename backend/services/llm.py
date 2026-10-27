"""OpenAI client shared by the agent, voice and helper services (key: OPENAI_API_KEY)."""
import os
from typing import Optional

from openai import AsyncOpenAI

_client: Optional[AsyncOpenAI] = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:  # lazy: importing services (tests, tools) must not need the key
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


async def complete(system: str, text: str, model: str = "gpt-5.4-mini", image_b64: Optional[str] = None) -> str:
    """One system + user turn; returns the reply text."""
    content: str | list = text
    if image_b64:
        content = [{"type": "text", "text": text},
                   {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]
    res = await client().chat.completions.create(
        model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": content}])
    return res.choices[0].message.content or ""
