# Smoke/regression after git branch switch to 'emergency' (HEAD 58e33e7). Low Google quota usage.
import os
import subprocess

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", os.environ.get("EXPO_BACKEND_URL", "")).rstrip("/")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")

LAT, LNG = 38.5816, -121.4944


def _git(*args):
    return subprocess.run(["git", *args], cwd="/app", capture_output=True, text=True).stdout.strip()


# Git state
class TestGitState:
    def test_head_contains_main_merge(self):
        # was a pinned HEAD hash; now: emergency must contain the merged main 4c145cc
        assert subprocess.run(["git", "merge-base", "--is-ancestor", "4c145cc", "HEAD"], cwd="/app").returncode == 0

    def test_branch(self):
        assert _git("branch", "--show-current") == "emergency"

    def test_status_clean(self):
        # ignore testing-agent artifacts (this file, pytest xml, iteration reports)
        lines = [l for l in _git("status", "--short").splitlines()
                 if l.strip() and not l[3:].startswith(("backend/tests/", "test_reports/"))]
        allowed = {"?? .emergent/cron/", "?? .emergent/testing/.runtime/"}
        assert set(lines) <= allowed, lines

    def test_env_ignored(self):
        for p in ("backend/.env", "frontend/.env"):
            assert os.path.exists(f"/app/{p}")
            assert subprocess.run(["git", "check-ignore", "-q", p], cwd="/app").returncode == 0


# Health
def test_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=20)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["providers"]["places"] == "google"
    assert d["providers"]["agent"] == "gpt-5.4-mini"
    assert d["providers"]["tts"] == "elevenlabs"  # ELEVENLABS_API_KEY set
    assert d["providers"]["routing"] == "here"  # HERE_API_KEY set


# Agent (text) - one Google search
def test_agent_find_truck_stop():
    r = requests.post(f"{BASE_URL}/api/agent",
                      json={"text": "find a truck stop near me", "lat": LAT, "lng": LNG,
                            "session_id": "TEST_iter13_smoke"}, timeout=90)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] in ("options", "route_set")
    assert d["reply"]
    assert d["place"] and d["place"]["name"]
    assert d["category"] == "truck_stop"
    if d["status"] == "route_set":
        assert d["route"]["distance_mi"] > 0


# Validation
def test_agent_validation():
    r = requests.post(f"{BASE_URL}/api/agent", json={"text": "hi", "lat": 999, "lng": LNG}, timeout=20)
    assert r.status_code == 422


def test_onboarding_invalid_step():
    r = requests.post(f"{BASE_URL}/api/onboarding/parse", data={"step": "bogus"},
                      files={"audio": ("a.m4a", b"x", "audio/m4a")}, timeout=20)
    assert r.status_code == 422


def test_audio_not_found():
    r = requests.get(f"{BASE_URL}/api/voice/audio/nonexistent_id_iter13", timeout=40)
    assert r.status_code == 404


# Speak (pinned/cached line; should not re-generate)
def test_speak_cached_line():
    r = requests.post(f"{BASE_URL}/api/speak", json={"text": "Okay, one sec."}, timeout=60)
    assert r.status_code == 200, r.text
    url = r.json()["audio_url"]
    a = requests.get(f"{BASE_URL}{url}", timeout=40)
    assert a.status_code == 200
    assert a.headers["content-type"].startswith("audio/mpeg")
    assert len(a.content) > 500
