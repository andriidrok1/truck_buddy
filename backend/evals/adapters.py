"""How a case reaches the model. `chat_json` is today's classifier (one chat completion answering JSON);
`tools` is reserved for a function-calling variant of the same prompt."""
from typing import Awaitable, Callable

from models import TruckProfile

Adapter = Callable[[str, str, TruckProfile], Awaitable[dict]]


async def chat_json(text: str, context: str, truck: TruckProfile) -> dict:
    """services.agent.classify: SYSTEM prompt + "[context]\\nDriver said: text", parsed JSON."""
    from services import agent
    return await agent.classify(text, context, truck)


async def tools(text: str, context: str, truck: TruckProfile) -> dict:
    """Function-calling adapter: not built yet."""
    raise NotImplementedError("the tools adapter is a stub; use --adapter chat_json")


ADAPTERS: dict[str, Adapter] = {"chat_json": chat_json, "tools": tools}
