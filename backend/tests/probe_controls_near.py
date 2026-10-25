# T1 probe: control phrases end-to-end with an active route + find-near address stability
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

BASE_URL = [l.split("=", 1)[1].strip().strip('"') for l in open("/app/frontend/.env")
            if l.startswith("EXPO_PUBLIC_BACKEND_URL") or l.startswith("EXPO_BACKEND_URL")][0].rstrip("/")
H = {"Authorization": "Bearer " + requests.post(f"{BASE_URL}/api/auth/access-code", json={"code": "TRUCK2026"},
                                                timeout=30).json()["access_token"]}
SAC = {"lat": 38.58, "lng": -121.49}


def ask(text, sid, active=None):
    b = {"text": text, "session_id": sid, **SAC}
    if active:
        b["active_place"] = active
    return requests.post(f"{BASE_URL}/api/agent", json=b, headers=H, timeout=90).json()


sid0 = f"T1-ctl-{uuid.uuid4().hex[:6]}"
route = ask("take me to Reno, NV", sid0)
place = route["place"]
print("route:", route["status"], place["name"], place["id"])


def ctl(text):
    r = ask(text, f"T1-c-{uuid.uuid4().hex[:6]}", place)
    return f"{text!r}: {r['status']} actions={[a['type'] for a in r.get('actions') or []]} reply={r['reply']!r}"


def near(i):
    r = ask("find a truck stop near 2828 El Centro Rd, Sacramento", f"T1-n-{uuid.uuid4().hex[:6]}")
    return f"near#{i}: {r['status']} {r['reply']!r}"


with ThreadPoolExecutor(4) as ex:
    for line in ex.map(ctl, ["start the ride", "the stars are out", "let's go", "cancel the trip", "mute",
                             "recenter", "start the ride", "the stars are out"]):
        print(line)
    for line in ex.map(near, range(4)):
        print(line)
