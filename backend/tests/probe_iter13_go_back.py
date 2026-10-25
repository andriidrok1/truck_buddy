# Iter13 probe: all-rejected -> "go back to the first one" -> "take the Sierra one" (public URL)
import os
import sys
import uuid
import requests

BASE = (os.environ.get("EXPO_BACKEND_URL") or os.environ["EXPO_PUBLIC_BACKEND_URL"]).rstrip("/")
LAT, LNG = 38.5816, -121.4944


def agent(text, sid, active):
    body = {"text": text, "lat": LAT, "lng": LNG, "session_id": sid, "truck_profile": {"driver_name": "Mike"}}
    if active:
        body["active_place"] = active
    r = requests.post(f"{BASE}/api/agent", json=body, timeout=60)
    r.raise_for_status()
    return r.json()


def run(with_active=True):
    sid = f"TEST_{uuid.uuid4().hex[:12]}"
    active = None
    offered = []
    seq = ["I need fuel"] + ["not that one"] * 4 + ["yes, go back to the first one", "take the Sierra one"]
    out = []
    for t in seq:
        d = agent(t, sid, active if with_active else None)
        name = (d.get("place") or {}).get("name")
        print(f"  {t!r:40} -> {d['status']:10} {name!s:35} | {d['reply'][:90]}")
        if d["status"] == "route_set":
            active = d["place"]
            offered.append(name)
        out.append((t, d["status"], name, d["reply"]))
    return out, offered


ok = True
for wa in (True, False):
    print(f"=== with_active={wa}")
    out, offered = run(wa)
    allrej = [o for o in out[:5] if "every one I found nearby" in o[3]]
    go_back = out[5]
    sierra = out[6]
    c1 = bool(allrej)
    c2 = go_back[1] == "route_set" and go_back[2] in offered[:-1] if go_back[1] == "route_set" else False
    c3 = sierra[1] == "route_set" and "Sierra" in (sierra[2] or "")
    print(f"  all_rejected_line={c1} go_back_route_set={go_back[1]=='route_set'} ({go_back[2]}) sierra={c3}")
    ok &= c1 and go_back[1] == "route_set" and c3
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
