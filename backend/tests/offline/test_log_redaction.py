"""Provider keys never reach the log stream: HERE, ORS and EIA take theirs in the URL, and an httpx error message
carries the whole URL (audit 2026-11-13, services/redact.py)."""
import logging
import sys

import httpx
from conftest import run

from services import breakdown, geocode

SAC = {"lat": 38.5816, "lng": -121.4944}


SECRET = "HEREsecretKEY123"


def test_geocode_failure_never_logs_the_key(monkeypatch, fake_http, caplog):
    monkeypatch.setenv("HERE_API_KEY", SECRET)
    monkeypatch.setenv("ORS_API_KEY", "ORSsecretKEY456")
    fake_http.add("geocode.search.hereapi.com", "/", status=401, text="bad key")
    fake_http.add("discover.search.hereapi.com", "/", status=401, text="bad key")
    fake_http.add("api.openrouteservice.org", "/", status=403, text="bad key")
    fake_http.add("nominatim.openstreetmap.org", "/", json=[])
    with caplog.at_level(logging.INFO):
        run(geocode.geocode("Reno", **SAC))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "geocode failed" in text and "401" in text
    assert SECRET not in text and "ORSsecretKEY456" not in text


def test_breakdown_where_never_logs_the_key(monkeypatch, fake_http, caplog):
    monkeypatch.setenv("HERE_API_KEY", SECRET)

    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    fake_http.add("revgeocode.search.hereapi.com", "/", fn=boom)
    with caplog.at_level(logging.INFO):
        assert run(breakdown.where(**SAC)) == ""
    assert "reverse geocode failed" in caplog.text and SECRET not in caplog.text


def test_log_formatter_redacts_keys_in_urls_and_tracebacks():
    from services import redact
    fmt = redact.RedactingFormatter("%(message)s")
    url = f"https://router.hereapi.com/v8/routes?origin=1,2&apiKey={SECRET}&api_key=x2&key=x3"
    try:
        raise httpx.HTTPStatusError(f"Client error '401' for url '{url}'", request=httpx.Request("GET", url),
                                    response=httpx.Response(401))
    except httpx.HTTPStatusError:
        rec = logging.getLogger("t").makeRecord("t", logging.ERROR, __file__, 1, "failed %s", (url,), sys.exc_info())
    out = fmt.format(rec)
    assert SECRET not in out and "x2" not in out and "x3" not in out and "origin=1,2" in out
    assert redact.describe(httpx.HTTPStatusError("x", request=httpx.Request("GET", url),
                                                 response=httpx.Response(403))) == "HTTP 403"
