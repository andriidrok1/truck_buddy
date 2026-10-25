"""Iteration 8: single-flight for /api/parking and /api/speak, negative tip filtering."""
import os
import random
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL").rstrip("/")
LOG = "/var/log/supervisor/backend.err.log"


def _log_count(needle: str) -> int:
    out = subprocess.run(["grep", "-c", needle, LOG], capture_output=True, text=True).stdout.strip()
    return int(out or 0)


# Parking single-flight: two concurrent cold-cache checks of the same place -> one vision call
def test_parking_single_flight():
    name = f"TEST_SF_{uuid.uuid4().hex[:6]}"
    # Random point on a known truck stop area (I-80 W Sacramento) so cache is cold
    lat = round(38.5880 + random.uniform(-0.002, 0.002), 5)
    lng = round(-121.5450 + random.uniform(-0.002, 0.002), 5)
    place = {"id": name, "name": name, "category": "truck_stop", "lat": lat, "lng": lng, "tags": []}

    def call():
        t = time.time()
        r = requests.post(f"{BASE_URL}/api/parking", json={"place": place}, timeout=120)
        return r, time.time() - t

    with ThreadPoolExecutor(2) as ex:
        a, b = list(ex.map(lambda _: call(), range(2)))
    (ra, ta), (rb, tb) = a, b
    print(f"parking times {ta:.1f}s {tb:.1f}s")
    assert ra.status_code == 200 and rb.status_code == 200
    assert ra.json() == rb.json()
    time.sleep(1)
    n = _log_count(f"parking {name} ->")
    assert n == 1, f"expected 1 vision log for {name}, got {n}"
    # third call is cached and fast
    t = time.time()
    rc = requests.post(f"{BASE_URL}/api/parking", json={"place": place}, timeout=60)
    assert rc.status_code == 200 and rc.json() == ra.json()
    assert time.time() - t < 3
    assert _log_count(f"parking {name} ->") == 1
    d = ra.json()
    for tip in (d.get("entry_tip"), d.get("park_tip")):
        if tip:
            assert "no suitable" not in tip.lower() and "not visible" not in tip.lower()


# /api/speak single-flight: two concurrent identical texts -> same audio_url
def test_speak_single_flight():
    text = f"Test line {uuid.uuid4().hex[:6]}, keep the hammer down."

    def call():
        t = time.time()
        r = requests.post(f"{BASE_URL}/api/speak", json={"text": text}, timeout=60)
        return r, time.time() - t

    with ThreadPoolExecutor(2) as ex:
        (ra, ta), (rb, tb) = list(ex.map(lambda _: call(), range(2)))
    print(f"speak times {ta:.1f}s {tb:.1f}s")
    assert ra.status_code == 200 and rb.status_code == 200
    assert ra.json()["audio_url"] == rb.json()["audio_url"]
    audio = requests.get(f"{BASE_URL}{ra.json()['audio_url']}", timeout=30)
    assert audio.status_code == 200 and len(audio.content) > 1000
    t = time.time()
    rc = requests.post(f"{BASE_URL}/api/speak", json={"text": text}, timeout=60)
    assert rc.json()["audio_url"] == ra.json()["audio_url"]
    assert time.time() - t < 2


def test_speak_validation():
    assert requests.post(f"{BASE_URL}/api/speak", json={"text": ""}, timeout=30).status_code == 422


# Negative tip filter (unit)
@pytest.mark.parametrize("txt", [
    "No suitable semi entrance visible", "No suitable truck parking visible", "not visible",
    "Unclear", "none", "N/A", "Entrance is not visible from above", "Can't tell from the image",
    "Unable to determine", "unknown", "",  None,
])
def test_tip_negative_filtered(txt):
    from services.parking import _tip
    assert _tip(txt) is None


@pytest.mark.parametrize("txt", [
    "Enter from the east driveway off Industrial Blvd", "Park in the back lot rows on the north side",
    "Northern lot along the fence",
])
def test_tip_positive_kept(txt):
    from services.parking import _tip
    assert _tip(txt) == txt
