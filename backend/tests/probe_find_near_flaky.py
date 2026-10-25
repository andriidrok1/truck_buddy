# T1 probe: flakiness rate of find-near (Stockton delivery) + "and a hotel there"
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

BASE_URL = [l.split("=", 1)[1].strip().strip('"') for l in open("/app/frontend/.env")
            if l.startswith("EXPO_PUBLIC_BACKEND_URL") or l.startswith("EXPO_BACKEND_URL")][0].rstrip("/")
_tok = requests.post(f"{BASE_URL}/api/auth/access-code", json={"code": "TRUCK2026"}, timeout=30).json()["access_token"]
_orig = requests.post
requests.post = lambda *a, **k: _orig(*a, headers={"Authorization": f"Bearer {_tok}"}, **k)

API = f"{BASE_URL}/api"
SAC = {"lat": 38.58, "lng": -121.49}


def one(i):
    sid = f"T1-probe-{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{API}/agent", json={"text": "where can I park close to my delivery in Stockton",
                                             "session_id": sid, **SAC}, timeout=90).json()
    if r["status"] != "route_set":
        return f"{i}: FIND FAIL status={r['status']} reply={r['reply']!r}"
    h = requests.post(f"{API}/agent", json={"text": "and a hotel there", "session_id": sid,
                                             "active_place": r["place"], **SAC}, timeout=90).json()
    ok = h.get("place") and h["place"]["category"] == "hotel" and abs(h["place"]["lat"] - 37.96) < 0.4
    return (f"{i}: find={r['reply']!r} | hotel={'OK' if ok else 'FAIL'} "
            f"{h['status']} {(h.get('place') or {}).get('name')} {(h.get('place') or {}).get('lat')} {h['reply']!r}")


with ThreadPoolExecutor(4) as ex:
    for line in ex.map(one, range(int(sys.argv[1]) if len(sys.argv) > 1 else 8)):
        print(line)
