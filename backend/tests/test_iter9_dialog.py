# Iteration 9: live dialog memory (session_id) on /api/agent and /api/voice
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL").rstrip("/")
LAT, LNG = 38.5816, -121.4944
T = 120


def agent(text, sid=None, active=None):
    body = {"text": text, "lat": LAT, "lng": LNG}
    if sid:
        body["session_id"] = sid
    if active:
        body["active_place"] = active
    r = requests.post(f"{BASE_URL}/api/agent", json=body, timeout=T)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def dialog():
    """Runs the 4-turn dialog once, shared by the assertions below."""
    sid = f"TEST_dlg_{uuid.uuid4().hex[:8]}"
    t1 = agent("I need fuel", sid)
    t2 = agent("not that one, the next closest", sid, t1.get("place"))
    t3 = agent("how far is it?", sid, t2.get("place") or t1.get("place"))
    t4 = agent("thanks, that's all", sid, t2.get("place") or t1.get("place"))
    return {"sid": sid, "t1": t1, "t2": t2, "t3": t3, "t4": t4}


# Dialog memory
class TestDialogMemory:
    def test_turn1_route_set(self, dialog):
        t1 = dialog["t1"]
        print("T1:", t1["reply"])
        assert t1["status"] == "route_set"
        assert t1["place"]["name"]
        assert t1.get("end_conversation") is False

    def test_turn2_different_place_from_earlier_results(self, dialog):
        t1, t2 = dialog["t1"], dialog["t2"]
        print("T2:", t2["status"], t2["reply"])
        assert t2["status"] == "route_set"
        assert t2["place"]["id"] != t1["place"]["id"]
        earlier = {t1["place"]["id"]} | {o["id"] for o in t1.get("options", [])}
        assert t2["place"]["id"] in earlier, "should pick from earlier results"
        assert t2.get("end_conversation") is False

    def test_turn2_options_include_previous_choice(self, dialog):
        ids = [o["id"] for o in dialog["t2"].get("options", [])]
        assert dialog["t1"]["place"]["id"] in ids

    def test_turn3_chat_about_active(self, dialog):
        t3 = dialog["t3"]
        print("T3:", t3["status"], t3["reply"])
        assert t3["status"] == "chat"
        assert "mile" in t3["reply"].lower() or "minute" in t3["reply"].lower()
        assert t3.get("end_conversation") is False

    def test_turn4_end_conversation(self, dialog):
        t4 = dialog["t4"]
        print("T4:", t4["status"], t4["reply"])
        assert t4["end_conversation"] is True
        assert "[END]" not in t4["reply"]
        assert t4["reply"].strip()


# Without session_id: independent requests (no memory)
class TestNoSession:
    def test_no_session_followup_has_no_memory(self):
        t = agent("not that one, the next closest")
        print("NOSESS:", t["status"], t["reply"])
        assert t["status"] in ("chat", "route_set", "not_understood", "no_results")
        assert "end_conversation" in t

    def test_no_session_fuel_route_set(self):
        t = agent("I need diesel")
        assert t["status"] == "route_set"
        assert t["end_conversation"] is False


# Validation
class TestValidation:
    def test_agent_session_id_too_long(self):
        r = requests.post(f"{BASE_URL}/api/agent", json={"text": "hi", "lat": LAT, "lng": LNG,
                                                         "session_id": "x" * 65}, timeout=T)
        assert r.status_code == 422

    def test_voice_session_id_too_long(self):
        with open("/tmp/q.mp3", "rb") as f:
            r = requests.post(f"{BASE_URL}/api/voice", files={"audio": ("q.mp3", f, "audio/mpeg")},
                              data={"lat": LAT, "lng": LNG, "session_id": "x" * 65}, timeout=T)
        assert r.status_code == 422

    def test_voice_with_session_id(self):
        sid = f"TEST_v_{uuid.uuid4().hex[:8]}"
        with open("/tmp/q.mp3", "rb") as f:
            r = requests.post(f"{BASE_URL}/api/voice", files={"audio": ("q.mp3", f, "audio/mpeg")},
                              data={"lat": LAT, "lng": LNG, "session_id": sid}, timeout=T)
        assert r.status_code == 200, r.text
        d = r.json()
        print("VOICE:", d["status"], d["transcript"], d["reply"])
        assert d["status"] == "route_set"
        assert d["audio_url"]
        assert d["end_conversation"] is False
