"""One shared keep-alive HTTP client for all external APIs (saves a TLS handshake per call).

Its request hook counts the paid per-call APIs (HERE, Gemini) and refuses one over its daily cap before it is
sent (services/spend.py)."""
from typing import Optional

import httpx

_client: Optional[httpx.AsyncClient] = None


async def _meter(request: httpx.Request) -> None:
    from services import spend  # lazy: spend uses this client for its alerts
    await spend.on_request(request)


HOOKS = {"request": [_meter]}


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=20,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=60),
            event_hooks=HOOKS,
        )
    return _client
