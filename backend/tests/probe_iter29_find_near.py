# T1 probe iter29: strict find-near (Stockton delivery) + "and a hotel there" success rate
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

BASE_URL = [l.split("=", 1)[1].strip().strip('"') for l in open("/app/frontend/.env")
            if l.startswith("EXPO_PUBLIC_BACKEND_URL") or l.startswith("EXPO_BACKEND_URL")][0].rstrip("/")
_tok = requests.post(f"{BASE_URL}/api/auth/access-code", json={"code": "TRUCK2026"}, timeout=30).json()["access_token"]
H = {"Authorization": f"Bearer {_tok}"}
API = f"{BASE_URL}/api"
SAC = {"lat": 38.58, "lng": -121.49}


def near_stockton(p):
    return bool(p) and abs(p["lat"] - 37.96) < 0.4 and abs(p["lng"] + 121.29) < 0.4


def one(i):
    sid = f"T1-i29-{uuid.uuid4().hex[:6]}"
    r = requests.post(f"{API}/agent", headers=H, json={"text": "where can I park close to my delivery in Stockton",
                                                        "session_id": sid, **SAC}, timeout=90).json()
    p = r.get("place")
    f_ok = (r["status"] == "route_set" and near_stockton(p) and "from your delivery" in r["reply"]
            and "miles from you" in r["reply"] and p["category"] == "truck_stop")
    line = f"{i}: FIND {'OK' if f_ok else 'FAIL'} {r['status']} cat={(p or {}).get('category')} reply={r['reply']!r}"
    if r["status"] != "route_set":
        return f_ok, False, line
    h = requests.post(f"{API}/agent", headers=H, json={"text": "and a hotel there", "session_id": sid,
                                                        "active_place": p, **SAC}, timeout=90).json()
    hp = h.get("place")
    h_ok = h["status"] == "route_set" and hp and hp["category"] == "hotel" and near_stockton(hp)
    return f_ok, h_ok, line + f" | HOTEL {'OK' if h_ok else 'FAIL'} {h['status']} {(hp or {}).get('name')} {(hp or {}).get('lat')} {h['reply']!r}"


n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
with ThreadPoolExecutor(4) as ex:
    res = list(ex.map(one, range(n)))
for _, _, line in res:
    print(line)
print(f"FIND ok {sum(r[0] for r in res)}/{n}  HOTEL ok {sum(r[1] for r in res)}/{n}")
