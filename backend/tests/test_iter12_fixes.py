# Iteration 12: verify iter-10 minor fixes - spoken() Saint/Doctor/24-7/periods/rounding, SENTENCE_END, all-rejected, captions, pinned /speak
import os
import re
import sys
import uuid

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from services.speech_text import spoken  # noqa: E402

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL").rstrip("/")
LAT, LNG = 38.5816, -121.4944
T = 120


# spoken(): requested fixes
@pytest.mark.parametrize("raw,expected", [
    ("St. Louis", "Saint Louis"),
    ("St. George", "Saint George"),
    ("Dr. Martin Luther King Jr Blvd", "Doctor Martin Luther King Jr Boulevard"),
    ("Main St, 4.7 miles", "Main Street, about four and a half miles"),
    ("Main St. Next exit", "Main Street. Next exit"),
    ("Open 24/7", "Open twenty-four seven"),
    ("0.2 mi", "less than half a mile"),
    ("0.7 mi", "about half a mile"),
    ("58 min", "about an hour"),
    ("US-50", "U.S. 50"),
])
def test_spoken_fixes(raw, expected):
    assert spoken(raw) == expected


# spoken(): regressions near the new rules
@pytest.mark.parametrize("raw,expected", [
    ("Elm St", "Elm Street"),
    ("Oak Dr, 3 mi", "Oak Drive, three miles"),
    ("Main St. Then turn left", "Main Street. Then turn left"),
    ("St. George, UT", "Saint George, UT"),
    ("US-50 & Howe Ave", "U.S. 50 and Howe Avenue"),
])
def test_spoken_regressions(raw, expected):
    assert spoken(raw) == expected


# server.SENTENCE_END: no split after St./Dr./U.S.
def test_sentence_end_split():
    from server import SENTENCE_END
    parts = SENTENCE_END.split("Okay, we're headed to St. Louis. That's 3 h.", maxsplit=1)
    assert parts == ["Okay, we're headed to St. Louis.", "That's 3 h."]
    assert SENTENCE_END.split("Take U.S. Route 50. Then exit.", maxsplit=1) == ["Take U.S. Route 50.", "Then exit."]
    assert SENTENCE_END.split("See Dr. King Blvd. It's close.", maxsplit=1)[0] != "See Dr."


# agent captions: no trailing .0 below 10 mi
def test_caption_miles():
    from services.agent import _miles
    assert _miles(5.0) == "5"
    assert _miles(4.7) == "4.7"
    assert _miles(12.4) == "12"
    assert _miles(1234.0) == "1,234"


def _agent(text, sid, active=None):
    body = {"text": text, "lat": LAT, "lng": LNG, "session_id": sid, "truck_profile": {"driver_name": "Mike"}}
    if active:
        body["active_place"] = active
    r = requests.post(f"{BASE_URL}/api/agent", json=body, timeout=T)
    assert r.status_code == 200, r.text
    return r.json()


# agent: after every truck stop rejected -> ALL_REJECTED, never a rejected place
@pytest.mark.parametrize("with_active", [False, True])
def test_all_rejected(with_active):
    sid = f"TEST_rej_{uuid.uuid4().hex[:8]}"
    res = _agent("I need fuel", sid)
    assert res["status"] == "route_set", res
    seen, active, final = [res["place"]["id"]], res["place"], None
    for i in range(7):
        res = _agent("not that one", sid, active if with_active else None)
        print(f"turn {i}: {res['status']} | {res.get('reply')}")
        if res["status"] == "route_set":
            assert res["place"]["id"] not in seen, f"rejected place re-offered: {res['place']['name']}"
            assert not re.search(r"\d\.0 miles", res["reply"]), res["reply"]
            seen.append(res["place"]["id"])
            active = res["place"]
        else:
            final = res
            break
    assert final is not None, f"never reached all-rejected after {len(seen)} places"
    assert final["status"] == "chat"
    assert final["reply"] == "That's every one I found nearby. Want me to go back to one of them?"
    assert len(seen) >= 1  # real demo stops: only the 49er is within 25 km of downtown Sacramento


# /api/speak pinned id
def test_speak_pinned():
    ids = []
    for _ in range(2):
        r = requests.post(f"{BASE_URL}/api/speak", json={"text": "Okay, one sec."}, timeout=T)
        assert r.status_code == 200, r.text
        ids.append(r.json()["audio_url"].rsplit("/", 1)[-1])
    assert ids[0] == ids[1] and re.fullmatch(r"[0-9a-f]{32}", ids[0]), ids
    a = requests.get(f"{BASE_URL}/api/voice/audio/{ids[0]}", timeout=T)
    assert a.status_code == 200 and a.headers["content-type"].startswith("audio/mpeg") and len(a.content) > 1000
    assert os.path.exists(os.path.join(os.path.dirname(__file__), "..", ".tts_cache", f"{ids[0]}.mp3"))
    with open("/tmp/iter12_pinned_id", "w") as f:
        f.write(ids[0])
