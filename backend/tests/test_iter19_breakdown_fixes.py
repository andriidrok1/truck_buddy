# Iteration 15 re-verification of iteration 14 breakdown fixes (FIX 1,2,3,4,5,6,7). Live: 1 research run.
# Run: pytest tests/test_iter19_breakdown_fixes.py -v -s -o addopts=''
import json
import os
import re
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", os.environ.get("EXPO_BACKEND_URL", "")).rstrip("/")
LAT, LNG = 38.5816, -121.4944
OUT = "/tmp/iter19_breakdown.json"
_state: dict = {}


def agent(text, sid, timeout=60):
    r = requests.post(f"{BASE_URL}/api/agent", json={"text": text, "lat": LAT, "lng": LNG, "session_id": sid},
                      timeout=timeout)
    assert r.status_code == 200, r.text[:300]
    return r.json()


@pytest.fixture(scope="module")
def s1():
    # FIX 1: tire kind kept after "trailer, can't roll" answer
    sid = f"t1s1-{uuid.uuid4().hex[:6]}"
    q = agent("I blew a tire", sid)
    a = agent("trailer, can't roll, come out to me", sid)
    res = None
    if a.get("research_id"):
        r = requests.get(f"{BASE_URL}/api/breakdown/{a['research_id']}", timeout=60)
        assert r.status_code == 200
        res = r.json()
    _state.update(q=q, a=a, res=res, sid=sid)
    json.dump(_state, open(OUT, "w"), indent=1, default=str)
    return _state


class TestFix1TireKind:
    def test_question(self, s1):
        assert s1["q"]["status"] == "chat" and "steer, drive or trailer" in s1["q"]["reply"], s1["q"]

    def test_researching(self, s1):
        assert s1["a"]["status"] == "researching", s1["a"]
        assert s1["a"]["breakdown_kind"] == "tire", s1["a"].get("breakdown_kind")

    def test_result_kind_and_card(self, s1):
        res = s1["res"]
        assert res and res["status"] == "providers", res and res.get("reply")
        assert res["breakdown_kind"] == "tire", res["breakdown_kind"]
        c = res["call_card"]
        print("call_card", c["problem"], "|", c["tire"])
        assert "blew a tire" in c["problem"].lower(), c["problem"]
        assert c["tire"] == "trailer tire", c["tire"]


class TestFix4Facts:
    def test_wait_source_and_prices(self, s1):
        res = s1["res"]
        if not res or res.get("status") != "providers":
            pytest.skip("no providers")
        for p in res["providers"]:
            f = p["facts"]
            if f.get("wait_time"):
                print("wait", p["name"], f["wait_time"])
                assert f["wait_time"].get("source") in ("review", "website"), f["wait_time"]
            for x in f.get("prices") or []:
                print("price", p["name"], x)
                assert not re.search(r"shower|diesel|fuel|gallon|food|meal", x["quote"], re.I), x
                assert re.search(r"repair|tire|tow|road ?call|service call|labor|per hour|hourly|hook|install|mount|patch",
                                 x["quote"], re.I), x


class TestFix7Summary:
    def test_summary_digits(self, s1):
        res = s1["res"]
        if not res or res.get("status") != "providers":
            pytest.skip("no providers")
        print("summary:", res.get("summary"))
        print("spoken:", res.get("reply"))
        assert not re.search(r"twenty[- ]four seven", res.get("summary") or "", re.I), res["summary"]

    def test_spoken_no_false_247(self, s1):
        res = s1["res"]
        if not res or res.get("status") != "providers":
            pytest.skip("no providers")
        spoken = res["reply"]
        for sent in re.split(r"(?<=[.!?])\s+", spoken):
            if not re.search(r"24/7|twenty[- ]four seven|around the clock", sent, re.I):
                continue
            for p in res["providers"]:
                word = [w for w in re.findall(r"[A-Za-z0-9']+", p["name"]) if len(w) >= 3][0].lower()
                if word in sent.lower():
                    assert p.get("listed_24_7") or p["facts"].get("hours_24_7") is True, (p["name"], sent)


class TestFix6Followup:
    def test_how_far_second(self, s1):
        res = s1["res"]
        if not res or res.get("status") != "providers" or len(res["providers"]) < 2:
            pytest.skip("need >=2 providers")
        r = agent("how far is the second one", s1["sid"])
        print("followup:", r["reply"], "| 2nd:", res["providers"][1]["name"], res["providers"][1]["distance_mi"])
        assert r["status"] == "chat"
        assert "don't mention that" not in r["reply"].lower(), r["reply"]


class TestFix3SkipParking:
    def test_route_skip_parking_fast(self, s1):
        res = s1["res"]
        if not res or res.get("status") != "providers":
            pytest.skip("no providers")
        p = res["providers"][0]
        place = {"id": p["id"], "name": p["name"], "category": "destination", "lat": p["lat"], "lng": p["lng"],
                 "address": p.get("address")}
        t = time.perf_counter()
        r = requests.post(f"{BASE_URL}/api/route", json={"lat": LAT, "lng": LNG, "place": place, "skip_parking": True},
                          timeout=30)
        dt = time.perf_counter() - t
        print(f"route skip_parking {dt:.2f}s")
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d.get("parking") is None, d.get("parking")
        assert d["route"]["summary"]
        assert dt < 3, dt


class TestFix2ChangedTopic:
    def test_never_mind_reno(self):
        sid = f"t1s2-{uuid.uuid4().hex[:6]}"
        q = agent("I blew a tire", sid)
        assert q["status"] == "chat" and "steer" in q["reply"], q
        a = agent("never mind, take me to Reno", sid)
        print("s2:", a["status"], a.get("reply"), a.get("research_id"))
        assert a["status"] != "researching" and not a.get("research_id"), a
        assert a["status"] in ("route_set", "chat", "options"), a["status"]


class TestFix5Mechanic:
    def test_check_engine_question(self):
        sid = f"t1s3-{uuid.uuid4().hex[:6]}"
        a = agent("my check engine light is on and I'm losing power", sid)
        print("s3:", a["status"], a.get("reply"), a.get("breakdown_kind"))
        assert a["status"] == "chat", a
        assert "Can it still drive, or does it need to come to you?" in a["reply"], a["reply"]
