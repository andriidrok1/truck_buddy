# Iteration 28: find near a named point (delivery address, town, shipper street)
import os
import uuid

import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"
DRIVER = {"lat": 38.58, "lng": -121.49}  # Sacramento


def _ask(text, sid):
    r = requests.post(f"{API}/agent", json={"text": text, "session_id": sid, **DRIVER}, timeout=90)
    assert r.status_code == 200, r.text
    return r.json()


def test_near_address():
    r = _ask("find a truck stop near 2828 El Centro Rd, Sacramento", f"t28-addr-{uuid.uuid4().hex[:6]}")
    assert r["status"] == "route_set", r
    assert "miles from" in r["reply"] and "miles from you" in r["reply"], r["reply"]


def test_near_town_delivery_and_follow_up_reuses_point():
    sid = f"t28-town-{uuid.uuid4().hex[:6]}"
    r = _ask("where can I park close to my delivery in Stockton", sid)
    assert r["status"] == "route_set", r
    assert "from your delivery" in r["reply"] and "miles from you" in r["reply"], r["reply"]
    p = r["place"]
    assert abs(p["lat"] - 37.96) < 0.4 and abs(p["lng"] + 121.29) < 0.4, (p["name"], p["lat"], p["lng"])  # Stockton
    assert p["distance_mi"] > 25  # list distances are from the driver (Sacramento -> Stockton ~45 mi)
    h = requests.post(f"{API}/agent", json={"text": "and a hotel there", "session_id": sid, "active_place": p,
                                            **DRIVER}, timeout=90).json()
    assert h["status"] == "route_set" and h["place"]["category"] == "hotel", h
    assert abs(h["place"]["lat"] - 37.96) < 0.4, (h["place"]["name"], h["place"]["lat"])


def test_near_failed_geocode_reads_back():
    r = _ask("find a truck stop near 99999 Zzyzx Qqqq Road, Nowhereville", "t28-fail")
    assert r["status"] == "chat", r
    assert r["reply"].startswith("I heard") and "can't find it" in r["reply"], r["reply"]
