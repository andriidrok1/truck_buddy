# Live API tests for the breakdown scenario (tire question -> research -> providers -> follow-ups).
# Keeps Google research runs to 2 (tire + tow). Run: pytest tests/test_iter18_breakdown_live.py -v -o addopts=''
import json
import os
import re
import uuid

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", os.environ.get("EXPO_BACKEND_URL", "")).rstrip("/")
LAT, LNG = 38.5816, -121.4944
SID = f"t1-{uuid.uuid4().hex[:6]}"
OUT = "/tmp/iter18_breakdown.json"
_state: dict = {}


def agent(text, sid=SID, timeout=60):
    r = requests.post(f"{BASE_URL}/api/agent", json={"text": text, "lat": LAT, "lng": LNG, "session_id": sid},
                      timeout=timeout)
    assert r.status_code == 200, r.text[:300]
    return r.json()


@pytest.fixture(scope="module")
def tire_result():
    # Breakdown: tire question then research
    q = agent("I blew a tire")
    _state["q"] = q
    assert q["status"] == "chat", q
    assert "steer, drive or trailer" in q["reply"], q["reply"]
    a = agent("trailer tire, can't roll, need them to come out")
    _state["a"] = a
    assert a["status"] == "researching" and a.get("research_id"), a
    assert a["reply"].startswith("Okay. Give me a minute"), a["reply"]
    r = requests.get(f"{BASE_URL}/api/breakdown/{a['research_id']}", timeout=60)
    assert r.status_code == 200
    res = r.json()
    _state["res"] = res
    json.dump(_state, open(OUT, "w"), indent=1, default=str)
    return res


class TestBreakdownTire:
    def test_providers_returned(self, tire_result):
        assert tire_result["status"] == "providers", tire_result.get("reply")
        ps = tire_result["providers"]
        assert 0 < len(ps) <= 6
        for p in ps:
            assert p["name"] and isinstance(p["distance_mi"], (int, float))
            assert "facts" in p
        assert any(p.get("phone") for p in ps)
        assert any(p.get("rating") for p in ps)

    def test_call_card(self, tire_result):
        c = tire_result["call_card"]
        assert c["gps"] and c["map_link"].startswith("https://maps.google.com/?q=")
        assert len(c["questions"]) >= 1 and c["share_text"]

    def test_timing(self, tire_result):
        total = tire_result["research_stats"]["stages_ms"]["total"]
        print("stats", tire_result["research_stats"])
        assert total < 20000, total

    def test_spoken_length(self, tire_result):
        assert len(tire_result["reply"].split()) <= 75, tire_result["reply"]

    def test_quotes_contain_amounts(self, tire_result):
        for p in tire_result["providers"]:
            f = p["facts"]
            for x in f.get("prices") or []:
                nums = {int(n) for n in re.findall(r"\d+", x["quote"].replace(",", ""))}
                assert int(x["amount_usd_low"]) in nums or int(x["amount_usd_high"]) in nums, x
            w = f.get("wait_time")
            if w:
                assert re.search(r"\d|hour|half", w["quote"], re.I), w

    def test_followup_call_cheapest(self, tire_result):
        r = agent("call the cheapest one")
        _state["call"] = r
        print("call:", r["reply"], r.get("dial_phone"))
        assert r["status"] == "chat" and r.get("dial_phone"), r

    def test_followup_how_far_second(self, tire_result):
        r = agent("how far is the second one")
        _state["far"] = r
        print("far:", r["reply"])
        assert r["status"] == "chat" and r["reply"]

    def test_followup_comchek(self, tire_result):
        r = agent("which one takes Comchek")
        print("comchek:", r["reply"])
        assert r["status"] == "chat" and r["reply"]

    def test_followup_not_hijacked(self, tire_result):
        r = agent("find me a truck stop")
        _state["ts"] = r
        json.dump(_state, open(OUT, "w"), indent=1, default=str)
        print("truckstop:", r["status"], r["reply"])
        assert r["status"] in ("route_set", "options"), r["status"]


class TestTowAndMisc:
    def test_tow_no_question(self):
        r = agent("I need a tow, truck won't move", sid=f"tow-{uuid.uuid4().hex[:6]}")
        print("tow:", r["status"], r["reply"])
        assert r["status"] == "researching" and r.get("research_id"), r
        _state["tow_id"] = r["research_id"]

    def test_unknown_id_404(self):
        r = requests.get(f"{BASE_URL}/api/breakdown/unknown-id", timeout=20)
        assert r.status_code == 404

    def test_normal_route(self):
        r = agent("take me to Reno", sid=f"n-{uuid.uuid4().hex[:6]}")
        assert r["status"] == "route_set", r

    def test_normal_hotel(self):
        r = agent("find me a hotel", sid=f"n-{uuid.uuid4().hex[:6]}")
        assert r["status"] in ("options", "route_set"), r

    def test_control_mute(self):
        r = agent("mute directions", sid=f"n-{uuid.uuid4().hex[:6]}")
        assert r.get("actions"), r
