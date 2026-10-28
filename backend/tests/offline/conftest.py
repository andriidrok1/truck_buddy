"""Offline test harness: no network, no keys, no database.

Three layers are available to tests in this directory:
- `fixture_json("here/route_sac_reno")` loads a recorded provider response (tests/fixtures).
- `fake_http` swaps the shared httpx client for one backed by `httpx.MockTransport`; every
  external call is routed by host + path prefix, and an unmocked call fails the test.
- `api_client` is a FastAPI TestClient with a device token; `fake_llm` cans the classifier.

Environment is set here, before any `services.*` import: `services.auth` binds RATE_PER_MIN at
import time and `server` loads .env, so the order matters.
"""
import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import pytest

from services import http  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

OFFLINE_ENV = {
    "JWT_SECRET": "test-secret-not-for-prod",
    "OPEN_ACCESS": "1",
    "RATE_LIMIT_PER_MIN": "0",
    "HERE_API_KEY": "test-here",
    "GOOGLE_MAPS_API_KEY": "",  # mock places provider; tests that want Google install FakePlaces instead
    "OPENAI_API_KEY": "test-openai",
    "PARKING_WARM": "0",
    "DEV_DOCS": "0",
}


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch):
    """Fake keys for every test in this directory only (restored after each test, so the live tests
    in tests/ and their skipif conditions never see them)."""
    for k, v in OFFLINE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("MEMORY_STORE", "off")  # no store unless a test asks for sqlite
    monkeypatch.delenv("EXPO_PUBLIC_BACKEND_URL", raising=False)


def fixture_json(name: str) -> Any:
    """tests/fixtures/<name>.json, e.g. 'here/route_sac_reno'."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


def run(coro):
    """Drive a coroutine to completion (the repo uses asyncio.run, not pytest-asyncio)."""
    return asyncio.run(coro)


# ---- HTTP -------------------------------------------------------------------------------------

@dataclass
class _Route:
    host: str
    path: str
    status: int = 200
    json: Any = None
    text: Optional[str] = None
    fn: Optional[Callable[[httpx.Request], httpx.Response]] = None
    times: Optional[int] = None
    hits: int = 0

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.hits += 1
        if self.fn:
            return self.fn(request)
        if self.json is not None:
            return httpx.Response(self.status, json=self.json, request=request)
        return httpx.Response(self.status, text=self.text or "", request=request)


class FakeHttp:
    """Routes requests by host and path prefix; first matching route wins; records every call."""

    def __init__(self):
        self.routes: list[_Route] = []
        self.calls: list[httpx.Request] = []

    def add(self, host: str, path: str = "/", *, json: Any = None, status: int = 200, text: Optional[str] = None,
            fn: Optional[Callable[[httpx.Request], httpx.Response]] = None, times: Optional[int] = None) -> "_Route":
        r = _Route(host=host, path=path, status=status, json=json, text=text, fn=fn, times=times)
        self.routes.append(r)
        return r

    def raise_for(self, host: str, path: str, exc: Exception) -> None:
        """Make calls to host/path raise (timeouts, connection errors)."""
        def boom(request: httpx.Request) -> httpx.Response:
            raise exc
        self.add(host, path, fn=boom)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        for r in self.routes:
            if request.url.host == r.host and request.url.path.startswith(r.path):
                if r.times is not None and r.hits >= r.times:
                    continue
                return r.respond(request)
        raise AssertionError(f"unmocked HTTP call: {request.method} {request.url.host}{request.url.path}")

    def calls_to(self, host: str, path: str = "/") -> list[httpx.Request]:
        return [c for c in self.calls if c.url.host == host and c.url.path.startswith(path)]


@pytest.fixture
def fake_http(monkeypatch) -> FakeHttp:
    router = FakeHttp()
    monkeypatch.setattr(http, "_client", httpx.AsyncClient(transport=httpx.MockTransport(router)))
    yield router
    http._client = None


def params_of(request: httpx.Request) -> list[tuple[str, str]]:
    """Query as (key, value) pairs in order; repeated keys stay repeated."""
    return list(request.url.params.multi_items())


# ---- LLM --------------------------------------------------------------------------------------

@dataclass
class FakeLlm:
    """Canned classifier: matches the 'Driver said: ...' line against rules; unmatched text fails."""
    rules: list[tuple[Any, dict]] = field(default_factory=list)
    calls: list[tuple[str, str, str]] = field(default_factory=list)
    cache_keys: list = field(default_factory=list)
    default: Optional[dict] = None

    def on(self, pattern: str, reply) -> "FakeLlm":
        """A dict reply is the classifier JSON; a str reply is returned as-is (summaries, gists)."""
        self.rules.append((re.compile(pattern, re.I), reply))
        return self

    usage: dict = field(default_factory=lambda: {"prompt_tokens": 2000, "completion_tokens": 40, "cached_tokens": 0})

    async def complete(self, system: str, text: str, model: str = "gpt-5.4-mini", image_b64=None) -> str:
        self.calls.append((system, text, model))
        m = re.search(r"Driver said: (.*)$", text, re.S)
        said = (m.group(1) if m else text).strip()
        for rx, reply in self.rules:
            if rx.search(said):
                return json.dumps(reply) if isinstance(reply, dict) else str(reply)
        if self.default is not None:
            return json.dumps(self.default) if isinstance(self.default, dict) else str(self.default)
        raise AssertionError(f"no canned LLM reply for: {said[:120]!r}")

    async def complete_full(self, system: str, text: str, model: str = "gpt-5.4-mini", image_b64=None,
                            cache_key: Optional[str] = None):
        """Same canned reply plus usage counters, recorded into the request timings like the real one."""
        from services import llm, timing
        self.cache_keys.append(cache_key)
        text_out = await self.complete(system, text, model, image_b64)
        for k in ("prompt_tokens", "completion_tokens", "cached_tokens"):
            if self.usage.get(k):
                timing.add(f"n_{k}", self.usage[k])
        return llm.LlmResult(text=text_out, usage=dict(self.usage))


@pytest.fixture
def fake_llm(monkeypatch) -> FakeLlm:
    from services import llm
    fake = FakeLlm()
    monkeypatch.setattr(llm, "complete", fake.complete)
    monkeypatch.setattr(llm, "complete_full", fake.complete_full)  # services do `llm.complete...` on the module
    return fake


# ---- Agent sandbox ----------------------------------------------------------------------------

class FakePlaces:
    """Places provider returning a fixed list; records the (category, lat, lng) it was asked."""
    source = "fake"

    def __init__(self, places=None):
        self.places = list(places or [])
        self.calls: list[tuple[str, float, float]] = []

    async def search(self, category, lat, lng):
        from models import PlacesResponse
        self.calls.append((category, lat, lng))
        found = [p for p in self.places if p.category == category]
        return PlacesResponse(category=category, radius_km=25.0, widened=False, places=found, source=self.source)

    async def get(self, place_id, lat, lng):
        return next((p for p in self.places if p.id == place_id), None)


@pytest.fixture
def agent_sandbox(monkeypatch, fake_llm):
    """Agent with no side effects: no satellite checks, no DB, no breakdown flow, fixed places/geocode."""
    from services import agent, approach, memory

    async def _none(*a, **k):
        return None

    async def _empty_list(*a, **k):
        return []

    agent._SESSIONS.clear()
    monkeypatch.setattr(agent, "check_parking", _none)
    monkeypatch.setattr(agent, "_background", lambda coro: coro.close())
    monkeypatch.setattr(approach, "background", lambda coro: coro.close() if hasattr(coro, "close") else None)
    monkeypatch.setattr(agent.breakdown, "handle", _none)
    monkeypatch.setattr(memory, "add_later", lambda *a, **k: None)
    monkeypatch.setattr(memory, "recent", _empty_list)
    places = FakePlaces()
    monkeypatch.setattr(agent, "get_places_provider", lambda: places)
    geocode_hits: dict[str, list] = {}

    async def geocode(q, lat, lng):
        for key, val in geocode_hits.items():
            if key.lower() in q.lower():
                return val
        return []

    monkeypatch.setattr(agent, "geocode", geocode)

    class Sandbox:
        llm = fake_llm
        places_provider = places
        geocode = geocode_hits  # map substring -> [Place]

        @staticmethod
        def provider():
            return agent.get_agent_provider()

    return Sandbox()


# ---- API --------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def api_client():
    """TestClient with a device token. Module-scoped, so it sets the env itself (the autouse
    fixture above is function-scoped); `services.auth` may already be imported with the default
    per-minute budget, hence the direct override of RATE_PER_MIN."""
    from fastapi.testclient import TestClient

    mp = pytest.MonkeyPatch()
    for k, v in OFFLINE_ENV.items():
        mp.setenv(k, v)
    mp.delenv("DATABASE_URL", raising=False)
    mp.setenv("MEMORY_STORE", "off")
    import server
    from services import auth
    mp.setattr(auth, "RATE_PER_MIN", 0)
    try:
        with TestClient(server.app) as c:
            token = c.post("/api/auth/device").json()["access_token"]
            c.headers.update({"Authorization": f"Bearer {token}"})
            yield c
    finally:
        mp.undo()
