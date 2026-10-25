import os

import requests

BASE = os.environ.get("EXPO_PUBLIC_BACKEND_URL", "").rstrip("/")


def _url():
    r = requests.post(f"{BASE}/api/speak", json={"text": "Hang on."}, timeout=60)
    r.raise_for_status()
    return BASE + r.json()["audio_url"]


def test_full_response_advertises_ranges():
    r = requests.get(_url(), timeout=30)
    assert r.status_code == 200
    assert r.headers["accept-ranges"] == "bytes"
    assert int(r.headers["content-length"]) == len(r.content) > 0


def test_ios_probe_range():
    url = _url()
    size = len(requests.get(url, timeout=30).content)
    r = requests.get(url, headers={"Range": "bytes=0-1"}, timeout=30)
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 0-1/{size}"
    assert r.headers["content-length"] == "2" and len(r.content) == 2


def test_open_and_suffix_ranges():
    url = _url()
    full = requests.get(url, timeout=30).content
    r = requests.get(url, headers={"Range": "bytes=10-"}, timeout=30)
    assert r.status_code == 206 and r.content == full[10:]
    r = requests.get(url, headers={"Range": "bytes=-100"}, timeout=30)
    assert r.status_code == 206 and r.content == full[-100:]


def test_unsatisfiable_and_missing():
    url = _url()
    assert requests.get(url, headers={"Range": "bytes=99999999-"}, timeout=30).status_code == 416
    assert requests.get(f"{BASE}/api/voice/audio/{'0' * 32}", timeout=30).status_code == 404
