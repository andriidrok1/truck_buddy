"""Open sign-up limit per IP (services/auth.register_device), set by DEVICES_PER_IP_PER_10MIN."""
import os
import subprocess
import sys
from pathlib import Path

from fastapi import HTTPException
from starlette.requests import Request

from services import auth

BACKEND = Path(__file__).resolve().parents[2]


def _request(ip: str) -> Request:
    return Request({"type": "http", "headers": [], "client": (ip, 1234)})


def test_the_sign_up_after_the_limit_gets_a_429_and_other_ips_are_not_affected(monkeypatch):
    monkeypatch.setenv("OPEN_ACCESS", "1")
    monkeypatch.setattr(auth, "MAX_DEVICES_PER_IP", 2)
    auth._signups.clear()
    try:
        auth.register_device(_request("10.0.0.1"))
        auth.register_device(_request("10.0.0.1"))
        try:
            auth.register_device(_request("10.0.0.1"))
            raise AssertionError("third sign-up from the same IP should be refused")
        except HTTPException as e:
            assert e.status_code == 429
        assert auth.register_device(_request("10.0.0.2"))
    finally:
        auth._signups.clear()


def _limit_with(env: dict) -> int:
    code = "from services import auth; print(auth.MAX_DEVICES_PER_IP)"
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env={**os.environ, **env},
                         capture_output=True, text=True, check=True)
    return int(out.stdout.strip().splitlines()[-1])


def test_the_limit_comes_from_the_env_and_falls_back_to_20():
    assert _limit_with({"DEVICES_PER_IP_PER_10MIN": "200"}) == 200
    assert _limit_with({"DEVICES_PER_IP_PER_10MIN": ""}) == 20
    assert _limit_with({"DEVICES_PER_IP_PER_10MIN": "abc"}) == 20


def test_zero_turns_the_limit_off(monkeypatch):
    monkeypatch.setenv("OPEN_ACCESS", "1")
    monkeypatch.setattr(auth, "MAX_DEVICES_PER_IP", 0)
    auth._signups.clear()
    try:
        for _ in range(5):
            assert auth.register_device(_request("10.0.0.3"))
    finally:
        auth._signups.clear()
