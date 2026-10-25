"""Iteration 21: access-code auth gate, rate limit, per-driver long-term memory."""
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
CODE = "TRUCK2026"
NO_AUTH = {"Authorization": None}  # conftest patches requests to add a token; None drops the header
LOC = {"lat": 39.7392, "lng": -104.9903}


def _token(code=CODE, ip=None):
    """With `ip`, calls the backend directly (no ingress), where X-Forwarded-For is the only hop;
    through the ingress a client-set header can't change the rate-limit key."""
    h = dict(NO_AUTH)
    if ip:
        h["X-Forwarded-For"] = ip
    base = "http://localhost:8001" if ip else BASE_URL
    return requests.post(f"{base}/api/auth/access-code", json={"code": code}, headers=h, timeout=30)


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(scope="module")
def tok_a():
    r = _token(ip=f"10.21.{uuid.uuid4().int % 250}.1")
    assert r.status_code == 200
    t = r.json()["access_token"]
    yield t
    requests.delete(f"{BASE_URL}/api/memory", headers=_h(t), timeout=30)


@pytest.fixture(scope="module")
def tok_b():
    t = _token(ip=f"10.22.{uuid.uuid4().int % 250}.1").json()["access_token"]
    yield t
    requests.delete(f"{BASE_URL}/api/memory", headers=_h(t), timeout=30)


# --- Access code redemption ---
class TestAccessCode:
    def test_correct_code_returns_token(self):
        r = _token(ip="10.30.0.1")
        assert r.status_code == 200
        d = r.json()
        assert d["token_type"] == "bearer" and isinstance(d["access_token"], str) and d["access_token"].count(".") == 2

    def test_lowercase_code_accepted(self):
        r = _token("  truck2026 ", ip="10.30.0.2")
        assert r.status_code == 200 and r.json()["access_token"]

    def test_wrong_code_401(self):
        r = _token("WRONG123", ip=f"10.31.{uuid.uuid4().int % 250}.9")
        assert r.status_code == 401
        assert r.json()["detail"] == "Invalid access code"

    def test_empty_code_422(self):
        r = _token("", ip="10.30.0.3")
        assert r.status_code == 422

    def test_rate_limit_429_after_5_wrong(self):
        ip = f"10.99.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"
        codes = [_token("NOPE", ip=ip).status_code for _ in range(5)]
        assert codes == [401] * 5
        r = _token("NOPE", ip=ip)
        assert r.status_code == 429
        # even the right code is blocked for this IP now
        assert _token(CODE, ip=ip).status_code == 429
        # a different IP is not affected
        assert _token(CODE, ip=f"10.98.{uuid.uuid4().int % 250}.1").status_code == 200


# --- Bearer protection on routes ---
PROTECTED = [
    ("GET", "/api/auth/check", None),
    ("GET", "/api/memory", None),
    ("DELETE", "/api/memory", None),
    ("GET", f"/api/places?category=fuel&lat={LOC['lat']}&lng={LOC['lng']}", None),
    ("POST", "/api/route", {}),
    ("POST", "/api/parking", {}),
    ("POST", "/api/parking/answer", {}),
    ("POST", "/api/agent", {"text": "hi", **LOC}),
    ("GET", "/api/breakdown/x", None),
    ("POST", f"/api/warm?lat={LOC['lat']}&lng={LOC['lng']}", None),
    ("POST", "/api/voice", None),
    ("POST", "/api/voice/transcribe", None),
    ("POST", "/api/speak", {"text": "hi"}),
    ("POST", "/api/onboarding/parse", None),
]


class TestProtection:
    @pytest.mark.parametrize("method,path,body", PROTECTED)
    def test_401_without_token(self, method, path, body):
        r = requests.request(method, f"{BASE_URL}{path}", json=body, headers=NO_AUTH, timeout=30)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"

    def test_401_invalid_token(self):
        r = requests.get(f"{BASE_URL}/api/memory", headers=_h("abc.def.ghi"), timeout=30)
        assert r.status_code == 401

    def test_all_openapi_routes_protected(self):
        spec = requests.get(f"{BASE_URL}/api/openapi.json", headers=NO_AUTH, timeout=30).json()
        public = {"/api/health", "/api/auth/access-code", "/api/voice/audio/{audio_id}", "/api/satellite"}
        open_routes = []
        for path, ops in spec["paths"].items():
            if path in public:
                continue
            for m in ops:
                url = f"{BASE_URL}{path}".replace("{rid}", "x").replace("{audio_id}", "x")
                r = requests.request(m.upper(), url, headers=NO_AUTH, timeout=30)
                if r.status_code != 401:
                    open_routes.append((m, path, r.status_code))
        assert not open_routes, open_routes

    def test_public_health(self):
        r = requests.get(f"{BASE_URL}/api/health", headers=NO_AUTH, timeout=30)
        assert r.status_code == 200 and r.json()["ok"] is True

    def test_auth_check_with_token(self, tok_a):
        r = requests.get(f"{BASE_URL}/api/auth/check", headers=_h(tok_a), timeout=30)
        assert r.status_code == 200 and r.json() == {"ok": True}


# --- Per-driver memory ---
class TestMemory:
    def test_new_driver_memory_empty(self, tok_b):
        r = requests.get(f"{BASE_URL}/api/memory", headers=_h(tok_b), timeout=30)
        assert r.status_code == 200 and r.json()["turns"] == []

    def test_agent_turn_saved_and_isolated(self, tok_a, tok_b):
        r = requests.post(f"{BASE_URL}/api/agent", headers=_h(tok_a), timeout=90,
                          json={"text": "find me a truck stop", **LOC, "session_id": "s-a1"})
        assert r.status_code == 200, r.text
        assert r.json()["place"] is not None
        turns = requests.get(f"{BASE_URL}/api/memory", headers=_h(tok_a), timeout=30).json()["turns"]
        assert [t["role"] for t in turns[-2:]] == ["driver", "copilot"]
        assert turns[-2]["text"] == "find me a truck stop"
        assert "_id" not in turns[-1] and "driver" not in turns[-1]
        other = requests.get(f"{BASE_URL}/api/memory", headers=_h(tok_b), timeout=30).json()["turns"]
        assert all(t["text"] != "find me a truck stop" for t in other)

    def test_long_term_recall_across_sessions(self, tok_b):
        sa, sb = f"sA-{uuid.uuid4().hex[:6]}", f"sB-{uuid.uuid4().hex[:6]}"
        r1 = requests.post(f"{BASE_URL}/api/agent", headers=_h(tok_b), timeout=90,
                           json={"text": "Remember this: my dog's name is Biscuit.", **LOC, "session_id": sa})
        assert r1.status_code == 200, r1.text
        r2 = requests.post(f"{BASE_URL}/api/agent", headers=_h(tok_b), timeout=90,
                           json={"text": "What is my dog's name?", **LOC, "session_id": sb})
        assert r2.status_code == 200, r2.text
        assert "biscuit" in r2.json()["reply"].lower(), r2.json()["reply"]

    def test_clear_memory(self, tok_b):
        r = requests.delete(f"{BASE_URL}/api/memory", headers=_h(tok_b), timeout=30)
        assert r.status_code == 200 and r.json()["deleted"] >= 2
        assert requests.get(f"{BASE_URL}/api/memory", headers=_h(tok_b), timeout=30).json()["turns"] == []

    def test_forgotten_after_clear(self, tok_b):
        r = requests.post(f"{BASE_URL}/api/agent", headers=_h(tok_b), timeout=90,
                          json={"text": "What is my dog's name?", **LOC, "session_id": f"sC-{uuid.uuid4().hex[:6]}"})
        assert r.status_code == 200
        assert "biscuit" not in r.json()["reply"].lower(), r.json()["reply"]

    def test_memory_limit_validation(self, tok_a):
        r = requests.get(f"{BASE_URL}/api/memory?limit=0", headers=_h(tok_a), timeout=30)
        assert r.status_code == 422
