"""The process is disposable: TTS clips on disk, breakdown results and parking verdicts in the store, a Place
Details fallback, per-worker budgets, a draining shutdown, and the pollers' shared feed cache."""
import asyncio
import os
import time

import pytest
from conftest import fixture_json, run

from models import AgentResponse, Approach, ParkingCheck, Place
from services import auth, breakdown, chains, kv, leader, parking_store, settings, tpims, trip_events, voice, weather
from services.google_places import GooglePlacesProvider

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}


@pytest.fixture
def tts_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TTS_DIR", str(tmp_path / "tts"))
    settings.reload()
    voice._AUDIO_CACHE.clear()
    yield tmp_path / "tts"
    monkeypatch.delenv("TTS_DIR", raising=False)
    settings.reload()
    voice._AUDIO_CACHE.clear()


# ---- settings and budgets ----

def test_budgets_are_split_between_workers(monkeypatch):
    monkeypatch.setenv("WORKERS", "3")
    s = settings.reload()
    assert s.workers == 3 and s.per_worker(40) == 14 and s.per_worker(300) == 100 and s.per_worker(0) == 0 and s.per_worker(1) == 1
    monkeypatch.delenv("WORKERS")
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    assert settings.reload().workers == 2
    monkeypatch.delenv("WEB_CONCURRENCY")
    assert settings.reload().workers == 1


def test_take_token_uses_this_workers_share(monkeypatch):
    monkeypatch.setenv("WORKERS", "4")
    settings.reload()
    monkeypatch.setattr(auth, "RATE_PER_MIN", 4)  # 4/min across 4 workers = 1 per worker
    auth._buckets.clear()
    assert auth.take_token("d", 1000.0) is True
    assert auth.take_token("d", 1000.1) is False
    monkeypatch.setattr(auth, "RATE_PER_MIN", 0)
    assert auth.take_token("d", 1000.2) is True  # 0 = off, whatever the worker count
    monkeypatch.delenv("WORKERS")
    settings.reload()


# ---- TTS clips on disk ----

def test_clips_are_written_to_disk_and_read_back_without_the_memory_cache(tts_dir):
    run(voice.store_clip("a" * 32, b"mp3-bytes"))
    assert (tts_dir / ("a" * 32 + ".mp3")).read_bytes() == b"mp3-bytes"
    voice._AUDIO_CACHE.clear()
    assert voice.get_audio("a" * 32) == b"mp3-bytes"  # another worker, or this one after a restart
    assert voice.get_audio("not-an-id") is None


def test_audio_route_serves_a_clip_another_worker_made(api_client, tts_dir):
    run(voice.store_clip("b" * 32, b"clip-from-worker-a"))
    voice._AUDIO_CACHE.clear()
    r = api_client.get(f"/api/voice/audio/{'b' * 32}")
    assert r.status_code == 200 and r.content == b"clip-from-worker-a"


def test_wait_audio_polls_the_disk_for_a_clip_still_being_made(tts_dir, monkeypatch):
    monkeypatch.setattr(voice, "CROSS_WORKER_WAIT_S", 1.5)

    async def scenario():
        async def later():
            await asyncio.sleep(0.5)
            await voice.store_clip("c" * 32, b"late")
        asyncio.create_task(later())
        t0 = time.monotonic()
        data = await voice.wait_audio("c" * 32)
        return data, time.monotonic() - t0
    data, took = run(scenario())
    assert data == b"late" and 0.4 < took < 1.5
    assert run(voice.wait_audio("d" * 32)) is None  # never made: a 404 after the wait


def test_pins_live_apart_and_eviction_leaves_them(tts_dir, monkeypatch):
    voice._AUDIO_CACHE["e" * 32] = b"hang on"
    stable = voice.pin("e" * 32, "filler:hang on")
    assert (tts_dir / "pins" / f"{stable}.mp3").exists() and voice.pinned("filler:hang on") == stable
    run(voice.store_clip("f" * 32, b"old"))
    old = tts_dir / ("f" * 32 + ".mp3")
    os.utime(old, (time.time() - 2 * voice.CLIP_TTL_S, time.time() - 2 * voice.CLIP_TTL_S))
    assert voice._evict_sync(time.time()) == 1 and not old.exists()
    voice._AUDIO_CACHE.clear()
    assert voice.get_audio(stable) == b"hang on"


def test_synthesize_later_writes_under_the_handed_out_id(tts_dir, monkeypatch):
    async def fake_synth(text, *a, audio_id=None, **k):
        voice._AUDIO_CACHE[audio_id] = b"made"
        await voice.store_clip(audio_id, b"made")
        return audio_id
    monkeypatch.setattr(voice, "synthesize", fake_synth)

    async def go():
        aid = voice.synthesize_later("later text")
        return aid, await voice.wait_audio(aid)
    aid, data = run(go())
    assert data == b"made" and (tts_dir / f"{aid}.mp3").exists()


# ---- breakdown jobs ----

def test_jobs_store_round_trip_and_prune(sqlite_store):
    run(kv.job_put("rid-1", "drv_1", "breakdown", {"status": "researching"}))
    assert run(kv.job_get("rid-1")) == {"status": "researching"}
    run(kv.job_put("rid-1", "drv_1", "breakdown", {"status": "providers", "reply": "x"}))
    assert run(kv.job_get("rid-1"))["status"] == "providers"
    assert run(kv.job_get("nope")) is None
    assert run(kv.job_prune(hours=0)) == 1 and run(kv.job_get("rid-1")) is None


def test_breakdown_result_is_read_from_the_store_on_another_worker(sqlite_store, monkeypatch):
    monkeypatch.setattr(breakdown, "RESULT_POLL_S", 0.05)
    done = AgentResponse(status="providers", transcript="", reply="Two shops nearby.", source="test").model_dump(mode="json")
    run(kv.job_put("rid-done", "drv_1", "breakdown", done))
    breakdown._JOBS.clear()  # this worker never ran it
    res = run(breakdown.result("rid-done"))
    assert res is not None and res.status == "providers" and res.reply == "Two shops nearby."
    assert run(breakdown.result("rid-unknown")) is None

    async def researching_then_done():
        await kv.job_put("rid-slow", "drv_1", "breakdown", {"status": "researching"})

        async def finish():
            await asyncio.sleep(0.3)
            await kv.job_put("rid-slow", "drv_1", "breakdown", done)
        asyncio.create_task(finish())
        return await breakdown.result("rid-slow")
    assert run(researching_then_done()).status == "providers"
    run(kv.job_put("rid-err", "drv_1", "breakdown", {"status": "error", "error": "Boom"}))
    assert run(breakdown.result("rid-err")).reply == breakdown.TOO_LONG


def test_breakdown_endpoint_answers_from_the_store(api_client, sqlite_store, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    me = auth.sub_of(api_client.headers["Authorization"].split()[1])
    done = AgentResponse(status="providers", transcript="", reply="Found them.", source="test").model_dump(mode="json")
    run(kv.job_put("rid-api", me, "breakdown", done))
    breakdown._JOBS.clear()
    r = api_client.get("/api/breakdown/rid-api")
    assert r.status_code == 200 and r.json()["reply"] == "Found them."


def test_breakdown_result_is_only_for_the_driver_who_asked(api_client, sqlite_store, monkeypatch):
    """Another driver's research id is an unknown id: the result holds that driver's shops, summary and position."""
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    done = AgentResponse(status="providers", transcript="", reply="Found them.", source="test").model_dump(mode="json")
    run(kv.job_put("rid-theirs", "drv_somebody_else", "breakdown", done))
    run(kv.job_put("rid-nobody", None, "breakdown", done))  # started without a token (dev): there is no owner to guard
    breakdown._JOBS.clear()
    r = api_client.get("/api/breakdown/rid-theirs")
    assert r.status_code == 404 and r.json() == {"detail": "Unknown research id"}
    assert api_client.get("/api/breakdown/rid-nobody").status_code == 200
    tester = {"Authorization": f"Bearer {auth._token('tester')}"}  # the tester screen reads across drivers
    assert api_client.get("/api/breakdown/rid-theirs", headers=tester).json()["reply"] == "Found them."


def test_a_running_breakdown_research_is_only_for_the_driver_who_asked(monkeypatch):
    """The same rule while the research still runs in this worker (no store involved)."""
    async def go():
        async def research():
            return AgentResponse(status="providers", transcript="", reply="Found them.", source="test")
        task = asyncio.create_task(research())
        monkeypatch.setitem(breakdown._JOBS, "rid-live", task)
        monkeypatch.setitem(breakdown._OWNERS, "rid-live", "drv_me")
        return await breakdown.result("rid-live", "drv_other"), await breakdown.result("rid-live", "drv_me")
    theirs, mine = run(go())
    assert theirs is None and mine.reply == "Found them."


# ---- parking verdicts and approaches ----

def _check(verdict="likely"):
    return ParkingCheck(verdict=verdict, label="Likely", reason="big lot", source="satellite")


def test_parking_verdicts_and_approaches_survive_a_restart(sqlite_store):
    parking_store._reset_for_tests()

    async def scenario():
        parking_store.save("38.65000,-121.38000", _check(), retried=False)
        parking_store.approach_set("38.65000,-121.38000", Approach(bearing=90, compass="west", street="Truck Stop Way"))
        await asyncio.gather(*parking_store.pending())
        rows = await kv.parking_all()
        parking_store._reset_for_tests()  # a fresh process
        assert parking_store.get("38.65000,-121.38000") is None
        n = await parking_store.sync_from_db()
        return rows, n
    rows, n = run(scenario())
    assert n == 2 and {r[0] for r in rows} == {"38.65000,-121.38000", "approach:38.65000,-121.38000"}
    assert parking_store.get("38.65000,-121.38000").verdict == "likely"
    assert parking_store.needs_retry("38.65000,-121.38000") is True  # no entry point yet
    assert parking_store.approach_get("38.65000,-121.38000").street == "Truck Stop Way"

    async def gone():
        parking_store.drop("38.65000,-121.38000")
        await asyncio.gather(*parking_store.pending())
        return await kv.parking_all()
    assert all(r[0] != "38.65000,-121.38000" for r in run(gone()))


def test_parking_store_falls_back_to_files_when_the_store_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "off")
    monkeypatch.setattr(parking_store, "DIR", tmp_path / "pc")
    parking_store._reset_for_tests()
    parking_store.save("k1", _check(), retried=True)
    assert (tmp_path / "pc" / "k1.json").exists() and parking_store.pending() == set()
    parking_store._reset_for_tests()
    parking_store.load_all()
    assert parking_store.get("k1").verdict == "likely"


# ---- Google Place Details ----

def test_google_get_falls_back_to_place_details(fake_http, monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-google")
    fake_http.add("places.googleapis.com", "/v1/places/ChIJabc123def456", json={
        "id": "ChIJabc123def456", "displayName": {"text": "Pilot Travel Center"}, "types": ["truck_stop", "gas_station"],
        "primaryType": "truck_stop", "location": {"latitude": 38.65, "longitude": -121.38},
        "formattedAddress": "1001 Truck Stop Way, Sacramento, CA", "rating": 4.1, "userRatingCount": 500})
    prov = GooglePlacesProvider()
    place = run(prov.get("ChIJabc123def456", **SAC))
    assert place is not None and place.name == "Pilot Travel Center" and place.category == "truck_stop"
    assert prov._seen["ChIJabc123def456"] is place  # remembered for the next lookup
    assert run(prov.get("ChIJabc123def456", **SAC)) is place and len(fake_http.calls_to("places.googleapis.com")) == 1
    req = fake_http.calls_to("places.googleapis.com")[0]
    assert req.headers["X-Goog-FieldMask"].startswith("id,displayName") and "places." not in req.headers["X-Goog-FieldMask"]
    assert run(prov.get("here-town-x", **SAC)) is None  # not a Google id: the demo list, which has no such place


# ---- feed cache and leader ----

def test_feed_cache_round_trip(sqlite_store):
    run(kv.feed_put("nws_alerts", [{"properties": {"event": "Flood Warning"}}]))
    payload, at = run(kv.feed_get("nws_alerts"))
    assert payload[0]["properties"]["event"] == "Flood Warning" and at.tzinfo is not None
    assert run(kv.feed_get("nothing")) is None


def test_leader_is_everyone_without_postgres(sqlite_store):
    assert run(leader.try_lead("nws_alerts")) is True


def test_a_follower_takes_the_leaders_feed_instead_of_fetching(sqlite_store, fake_http, monkeypatch):
    """No NWS route is mocked: a fetch would fail the test; the shared copy is served."""
    weather._feed_cache.clear()
    run(kv.feed_put(weather.FEED_NAME, [{"properties": {"event": "High Wind Warning"}, "geometry": None}]))
    alerts = run(weather._shared_feed(time.monotonic()))
    assert alerts and alerts[0]["properties"]["event"] == "High Wind Warning"
    assert isinstance(weather._feed_cache.get("at"), float)
    weather._feed_cache.clear()


def test_chains_and_tpims_share_through_the_cache(sqlite_store):
    from models import ChainControl
    chains._cache.clear()
    chains._cache[3] = (time.monotonic(), [ChainControl(id="c1", route="I-80", location="Kingvale", status="R-2", index=0, lat=39.3, lng=-120.4)])
    run(chains.share())
    chains._cache.clear()
    assert run(chains.take_shared()) is True and chains._cache[3][1][0].location == "Kingvale"
    tpims._sites.clear()
    tpims._live.clear()
    tpims._sites["s1"] = tpims.Site(site_id="s1", name="Donner Summit", lat=39.3, lng=-120.3, capacity=40, state="CA")
    tpims._live["s1"] = tpims.Live(site_id="s1", available=12, open=True)
    run(tpims.share())
    tpims._sites.clear()
    tpims._live.clear()
    assert run(tpims.take_shared()) is True and tpims._sites["s1"].capacity == 40 and tpims._live["s1"].available == 12
    chains._cache.clear()  # not for the route tests that follow
    tpims._sites.clear()
    tpims._live.clear()


def test_lock_calls_are_serialised_and_one_failure_hides_nothing(monkeypatch):
    """Three feeds ask at startup at once: the calls go one at a time over the single connection, and a feed
    whose call fails polls locally without touching the others' answers."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("MEMORY_STORE", "pg")
    leader._held.clear()
    busy = {"now": 0, "max": 0}

    class FakeConn:
        def is_closed(self):
            return False

        async def fetchval(self, sql, key):
            busy["now"] += 1
            busy["max"] = max(busy["max"], busy["now"])
            try:
                await asyncio.sleep(0.02)
                if key == leader._key("caltrans_chains"):
                    raise RuntimeError("another operation is in progress")
                return True
            finally:
                busy["now"] -= 1

    async def fake_connection():
        return FakeConn()
    monkeypatch.setattr(leader, "_connection_locked", fake_connection)

    async def scenario():
        return await asyncio.gather(leader.try_lead("nws_alerts"), leader.try_lead("caltrans_chains"), leader.try_lead("tpims"))
    assert run(scenario()) == [True, True, True]
    assert busy["max"] == 1  # never two operations on the connection at once
    assert leader._held == {"nws_alerts", "tpims"}  # the failed one is not recorded as held: it polls locally
    leader._held.clear()


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL (a throwaway Postgres)")
def test_three_feeds_lock_at_once_on_postgres(monkeypatch):
    """Startup: NWS, Caltrans and TPIMS all ask for their lock concurrently over the one connection."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("MEMORY_STORE", "pg")

    async def scenario():
        got = await asyncio.gather(leader.try_lead("t_nws"), leader.try_lead("t_chains"), leader.try_lead("t_tpims"))
        for n in ("t_nws", "t_chains", "t_tpims"):
            await leader.release(n)
        await leader.close()
        return got
    assert run(scenario()) == [True, True, True]


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL (a throwaway Postgres)")
def test_only_one_session_leads_on_postgres(monkeypatch):
    """Two connections to the same database: the second does not get the lock the first holds."""
    import asyncpg
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("MEMORY_STORE", "pg")

    async def scenario():
        from services import db
        first = await leader.try_lead("test_lock")
        other = await asyncpg.connect(db._dsn())
        try:
            second = bool(await other.fetchval("SELECT pg_try_advisory_lock($1)", leader._key("test_lock")))
        finally:
            await other.close()
        await leader.release("test_lock")
        await leader.close()
        return first, second
    first, second = run(scenario())
    assert first is True and second is False


# ---- shutdown drains background writes ----

def test_shutdown_waits_for_background_writes(sqlite_store, fake_http, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    from fastapi.testclient import TestClient
    import server
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    place = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138).model_dump()
    with TestClient(server.app) as c:
        token = c.post("/api/auth/device").json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        r = c.post("/api/reroute", json={"lat": 38.9, "lng": -121.07, "heading": 80, "place": place, "handle": None,
                                         "section_index": 0, "traveled_m": 0, "trip_id": "trip-drain"})
        assert r.status_code == 200
    # the client left: shutdown drained the trip event written in the background
    import base64
    import json
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    driver = json.loads(base64.urlsafe_b64decode(payload))["sub"]
    events = run(trip_events.for_trip(driver, "trip-drain"))
    assert [e["kind"] for e in events] == ["reroute_requested"]
