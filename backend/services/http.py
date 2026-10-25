"""One shared keep-alive HTTP client for all external APIs (saves a TLS handshake per call)."""
from typing import Optional

import httpx

_client: Optional[httpx.AsyncClient] = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=20,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=60),
        )
    return _client
