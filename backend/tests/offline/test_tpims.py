"""Live truck parking (TPIMS) and public-lot capacity (NTAD): parsing, staleness, matching, speech, wiring."""
from datetime import datetime, timedelta, timezone

import httpx
from conftest import fixture_json, run

from models import AgentRequest, Place
from services import places, tpims
from services.tpims import Live, Site, attach, capacity_near, parse_dynamic, parse_static, sentence

WI_DYN, WI_STATIC = fixture_json("tpims/wi_dynamic"), fixture_json("tpims/wi_static")
KY_DYN, KY_STATIC = fixture_json("tpims/ky_dynamic"), fixture_json("tpims/ky_static")
IL_DYN = fixture_json("tpims/il_dynamic")
NOW = datetime(2026, 10, 28, 2, 50, tzinfo=timezone.utc)  # six minutes after the WI recording
PORTAGE = "WI00039IS0011300SRSTARE11"


def setup_function():
    tpims._sites.clear()
    tpims._live.clear()
    tpims._static_at.clear()


def test_parse_static_fields():
    sites = parse_static(WI_STATIC, "WI")
    s = sites[PORTAGE]
    assert s.name == "Portage Rest Area #11" and s.capacity == 68 and s.highway == "I-39/90/94"
    assert (s.lat, s.lng) == (43.428772, -89.483492) and s.direction == "EB" and s.state == "WI"
    assert parse_static([{"siteId": "x", "location": {"latitude": "a"}}]) == {} and parse_static("junk") == {}
    assert parse_static([{"siteId": "y", "location": {"latitude": 1, "longitude": 2}, "capacity": 0}])["y"].capacity \
        is None


def test_parse_dynamic_shapes():
    live = parse_dynamic(WI_DYN, now=NOW)
    p = live[PORTAGE]
    assert p.available == 56 and p.capacity == 68 and p.trend == "STEADY" and p.open and not p.stale and not p.low
    assert p.updated == datetime(2026, 10, 28, 2, 44, tzinfo=timezone.utc)
    ky = parse_dynamic(KY_DYN, now=datetime(2026, 10, 27, 21, 50, tzinfo=timezone.utc))
    low = next(v for v in ky.values() if v.low)
    assert low.available is None and low.low and not low.stale
    il = parse_dynamic(IL_DYN, now=datetime(2026, 10, 27, 21, 55, tzinfo=timezone.utc))
    unknown = [v for v in il.values() if v.available is None and not v.low]
    assert len(unknown) >= 15 and all(not v.stale for v in unknown)  # "Unknown" is fresh but says nothing
    numeric = next(v for v in il.values() if v.available is not None)
    assert numeric.available in (10, 22)


def test_dynamic_edge_values():
    rows = [{"siteId": "a", "reportedAvailable": 7, "timeStamp": NOW.isoformat()},
            {"siteId": "b", "reportedAvailable": "  12 ", "timeStamp": NOW.isoformat(), "open": False},
            {"siteId": "c", "reportedAvailable": -3, "timeStamp": NOW.isoformat(), "trend": "filling"},
            {"siteId": "d", "reportedAvailable": True, "timeStamp": NOW.isoformat()},
            {"siteId": "e", "reportedAvailable": "LOW", "timeStamp": (NOW - timedelta(hours=1)).isoformat()},
            {"siteId": "f", "reportedAvailable": "5", "timeStamp": "2026-10-28T02:49:00"},  # naive = UTC
            {"siteId": "g", "reportedAvailable": "5"},  # no timestamp = stale
            {"siteId": "", "reportedAvailable": "5"}, "junk", None]
    live = parse_dynamic(rows, now=NOW)
    assert live["a"].available == 7 and live["b"].available == 12 and live["b"].open is False
    assert live["c"].available == 0 and live["c"].trend == "FILLING"
    assert live["d"].available is None
    assert live["e"].stale and live["e"].available is None and live["e"].low is False
    assert live["f"].available == 5 and not live["f"].stale
    assert live["g"].stale and live["g"].available is None
    assert set(live) == {"a", "b", "c", "d", "e", "f", "g"}


def test_refresh_fetches_static_once_a_day_and_keeps_old_data_on_failure(fake_http, monkeypatch):
    monkeypatch.setenv("TPIMS_FEEDS",
                       '{"WI": ["https://511wi.gov/api/TPIMS_Dynamic", "https://511wi.gov/api/TPIMS_Static"]}')
    monkeypatch.setattr(tpims, "DEFAULT_FEEDS", {})
    fake_http.add("511wi.gov", "/api/TPIMS_Static", json=WI_STATIC)
    fake_http.add("511wi.gov", "/api/TPIMS_Dynamic", json=WI_DYN, times=1)
    fake_http.add("511wi.gov", "/api/TPIMS_Dynamic", status=503, text="down")
    run(tpims.refresh(now=0.0))
    assert len(tpims._sites) == 14 and len(tpims._live) == 14
    run(tpims.refresh(now=100.0))  # static cached; dynamic fails -> old counts stay
    assert len(fake_http.calls_to("511wi.gov", "/api/TPIMS_Static")) == 1
    assert len(fake_http.calls_to("511wi.gov", "/api/TPIMS_Dynamic")) == 2 and len(tpims._live) == 14
    fake_http.routes.clear()
    fake_http.raise_for("511wi.gov", "/api/", httpx.ConnectError("no"))
    run(tpims.refresh(now=tpims.STATIC_TTL_S + 1))
    assert len(tpims._sites) == 14  # a failed static refresh keeps the sites


def test_feeds_env_parsing(monkeypatch):
    monkeypatch.delenv("TPIMS_FEEDS", raising=False)
    assert set(tpims.feeds()) == {"WI", "KY", "IL"}
    monkeypatch.setenv("TPIMS_FEEDS", '{"ks": ["https://a/d", "https://a/s"], "bad": "x"}')
    f = tpims.feeds()
    assert f["KS"] == ("https://a/d", "https://a/s") and "BAD" not in f and "WI" in f
    monkeypatch.setenv("TPIMS_FEEDS", "{not json")
    assert set(tpims.feeds()) == {"WI", "KY", "IL"}


def _portage_place(dlat=0.0, dlng=0.0, category="rest_area") -> Place:
    return Place(id="g-portage", name="Portage Rest Area", category=category, lat=43.428772 + dlat,
                 lng=-89.483492 + dlng)


def test_attach_live_counts_within_300m():
    tpims._sites.update(parse_static(WI_STATIC, "WI"))
    tpims._live.update(parse_dynamic(WI_DYN, now=NOW))
    near, far = _portage_place(dlat=0.0025), _portage_place(dlat=0.004)  # ~280 m / ~440 m north
    attach([near, far])
    assert near.parking_available == 56 and near.parking_capacity == 68 and near.parking_trend == "STEADY"
    assert near.parking_source == "tpims" and near.parking_updated == "2026-10-28T02:44:00+00:00"
    assert far.parking_available is None and far.parking_source in (None, "ntad")
    hotel = _portage_place(category="hotel")
    attach([hotel])
    assert hotel.parking_available is None and hotel.parking_source is None


def test_attach_stale_or_closed_gives_capacity_only():
    tpims._sites.update(parse_static(WI_STATIC, "WI"))
    stale = parse_dynamic(WI_DYN, now=NOW + timedelta(hours=2))
    tpims._live.update(stale)
    p = _portage_place()
    attach([p])
    assert p.parking_available is None and p.parking_capacity == 68 and p.parking_source == "tpims"
    tpims._live[PORTAGE] = Live(site_id=PORTAGE, available=3, open=False, updated=NOW, capacity=68)
    p = _portage_place()
    attach([p])
    assert p.parking_available is None and p.parking_capacity == 68


def test_capacity_from_ntad_for_places_without_sensors():
    assert places.has_data("truck_parking_capacity")
    assert capacity_near(38.88744, -121.97649) == 12  # Dunnigan, I-5
    assert capacity_near(38.89275, -121.97813) == 12  # our curated Dunnigan entry, ~600 m away
    assert capacity_near(39.5296, -119.8138) is None  # downtown Reno
    dunnigan = Place(id="ra-dunnigan", name="Dunnigan Rest Area", category="rest_area", lat=38.89275, lng=-121.97813)
    attach([dunnigan])
    assert dunnigan.parking_capacity == 12 and dunnigan.parking_source == "ntad" and dunnigan.parking_available is None


def test_sentences():
    base = dict(id="x", name="X", category="truck_stop", lat=0, lng=0)
    assert sentence(Place(**base, parking_available=56)) == " 56 truck spots open there right now."
    assert sentence(Place(**base, parking_available=0, parking_capacity=68)) == " Parking's full there right now."
    assert sentence(Place(**base, parking_low=True, parking_capacity=21)) == " Parking's almost full there."
    assert sentence(Place(**base, parking_capacity=18)) == " 18 truck spots there, no live count."
    assert sentence(Place(**base)) == ""
    assert sentence(Place(**{**base, "category": "hotel"}, parking_available=5)) == ""


def test_route_reply_carries_the_parking_line(agent_sandbox, fake_http):
    tpims._sites.update(parse_static(WI_STATIC, "WI"))
    tpims._live.update(parse_dynamic(WI_DYN, now=NOW))
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    portage = _portage_place()
    portage.address = "I-39, Columbia County, WI"
    portage.distance_mi = 4.0
    agent_sandbox.places_provider.places = [portage]
    agent_sandbox.llm.on(r"rest area", {"intent": "find", "category": "rest_area"})

    async def search(category, lat, lng):  # the sandbox provider doesn't attach; real ones do
        from models import PlacesResponse
        attach([portage])
        return PlacesResponse(category=category, radius_km=25.0, widened=False, places=[portage], source="fake")

    agent_sandbox.places_provider.search = search
    res = run(agent_sandbox.provider().handle(AgentRequest(text="find a rest area", session_id="tp", lat=43.4,
                                                           lng=-89.5), None))
    assert res.status == "route_set"
    assert " 56 truck spots open there right now." in res.reply
    assert res.place.parking_available == 56 and res.place.parking_source == "tpims"


def test_mock_provider_attaches(monkeypatch):
    tpims._sites.clear()
    res = run(places.MockPlacesProvider().search("rest_area", 38.89, -121.97))
    dunnigan = next(p for p in res.places if p.id == "ra-dunnigan")
    assert dunnigan.parking_capacity == 12 and dunnigan.parking_source == "ntad"


def test_poller_flag(monkeypatch):
    import asyncio

    async def check(flag):
        monkeypatch.setenv("TPIMS_ENABLED", flag)
        tpims._task = None
        await tpims.start()
        running = tpims._task is not None
        await tpims.stop()
        return running

    assert run(check("0")) is False
    monkeypatch.setattr(tpims, "refresh", lambda now=None: asyncio.sleep(0))
    assert run(check("1")) is True and tpims._task is None


def test_site_model_roundtrip():
    s = Site(site_id="a", name="n", lat=1.0, lng=2.0)
    assert s.capacity is None and s.model_dump()["state"] is None
