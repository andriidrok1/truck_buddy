"""Dialog state that survives JSON and a session store that lets another process (or a restart) continue the
dialog, including a question asked on the previous turn."""
import copy
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from conftest import fixture_json, run

from models import AgentRequest, Place, RouteInfo
from services import agent, db, session_store
from services.dialog_state import DialogState
from services.routing import _here_parse

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)
PILOT = Place(id="g-pilot-fernley", name="Pilot Travel Center, Fernley", category="truck_stop", lat=39.6, lng=-119.25,
              address="100 Pilot Rd, Fernley, NV", distance_mi=80.0)


def _populated() -> DialogState:
    route: RouteInfo = _here_parse(DATA)
    s = DialogState()
    s["history"].append("Driver: take me to Reno")
    s["places"] = [RENO, PILOT]
    s["city"] = "Reno, NV"
    s["pending"] = {"text": "stop at the Pilot", "kind": "stop_guard", "d": {"intent": "add_stop", "place": PILOT.model_dump()},
                    "no": {"reply": "Okay, no stop added."}, "yes": "via_far_yes"}
    s["via"] = {"place": RENO.id, "stops": [PILOT]}
    s["alternatives"] = {"place": RENO.id, "routes": list(route.alternatives), "i": 1,
                         "current": route.model_copy(update={"alternatives": []}), "refetch": False,
                         "origin": (38.58, -121.49), "profile": (162, 53, 80000, False, False, False)}
    s.setdefault("rejected", []).append("g-loves")
    s["facts"] = {"favorite_chains": ["Pilot"]}
    s["hos_cache"] = [1_700_000_000.0, {"drive_left_min": 90}]
    s["turns"] = 3
    return s


def test_dialog_state_round_trips_through_json_with_its_models():
    s = _populated()
    back = DialogState.from_json(s.to_json())
    assert back["history"] == s["history"] and back["city"] == "Reno, NV" and back["turns"] == 3
    assert isinstance(back["places"][0], Place) and back["places"][1].id == PILOT.id
    assert back["pending"]["yes"] == "via_far_yes" and back["pending"]["d"]["place"]["id"] == PILOT.id
    assert isinstance(back["via"]["stops"][0], Place)
    alts = back["alternatives"]
    assert all(isinstance(r, RouteInfo) for r in alts["routes"]) and isinstance(alts["current"], RouteInfo)
    assert alts["current"].geometry == s["alternatives"]["current"].geometry and alts["i"] == 1
    assert alts["origin"] == (38.58, -121.49) and alts["profile"] == (162, 53, 80000, False, False, False)
    assert back["rejected"] == ["g-loves"] and back["facts"] == {"favorite_chains": ["Pilot"]}
    assert back["hos_cache"][0] == 1_700_000_000.0


def test_dict_surface_matches_what_the_agent_expects():
    s = DialogState()
    assert "pending" not in s and "city" not in s and s.get("facts") is None and s.get("providers_at", 0) == 0
    s["pending"] = {"text": "x"}
    assert "pending" in s and s.pop("pending") == {"text": "x"} and "pending" not in s
    assert s.setdefault("rejected", []) is s["rejected"]  # the list, not a copy
    s["rejected"].append("a")
    assert s["rejected"] == ["a"] and "rejected" in s
    s["extra_key"] = 1  # extras are kept for anything a module writes before it is declared here
    assert s.get("extra_key") == 1 and "extra_key" in s and s.pop("extra_key") == 1


def test_db_store_saves_loads_sweeps_and_drops(sqlite_store, monkeypatch):
    monkeypatch.setenv("SESSION_STORE", "db")
    # earlier tests leave dialogs of random subs in this process; one that starts "drv_a" would be counted below
    session_store.memory_sessions().clear()
    store = session_store.DbSessionStore()
    assert session_store.kind() == "db" and isinstance(session_store.get_store(), session_store.DbSessionStore)
    s = _populated()
    s["driver"] = "drv_a"
    run(store.save("drv_a:dlg-1", s))
    back = run(store.load("drv_a:dlg-1"))
    assert back["pending"]["kind"] == "stop_guard" and isinstance(back["alternatives"]["current"], RouteInfo)
    assert run(store.load("drv_a:never")) == DialogState()  # unknown id: a fresh dialog
    # an old row goes on the next save once the sweep interval has passed
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    con = sqlite3.connect(str(sqlite_store))
    con.execute("INSERT INTO dialog_sessions (session_id, driver, state, updated_at) VALUES ('drv_z:old', 'drv_z', '{}', ?)", (old,))
    con.commit()
    con.close()
    store._last_sweep = float("-inf")
    run(store.save("drv_a:dlg-2", DialogState()))
    assert run(store.load("drv_z:old")) == DialogState()
    con = sqlite3.connect(str(sqlite_store))
    ids = {r[0] for r in con.execute("SELECT session_id FROM dialog_sessions")}
    con.close()
    assert ids == {"drv_a:dlg-1", "drv_a:dlg-2"}
    assert run(session_store.drop_prefix("drv_a")) == 2
    con = sqlite3.connect(str(sqlite_store))
    assert con.execute("SELECT count(*) FROM dialog_sessions").fetchone()[0] == 0
    con.close()


def _two_workers(agent_sandbox, fake_http):
    long = copy.deepcopy(DATA)
    for s in long["routes"][0]["sections"]:
        s["summary"]["duration"] *= 6  # the stop takes the trip from 2 h 52 min to 17 h: the guard asks first
    fake_http.add("router.hereapi.com", "/v8/routes",
                  fn=lambda request: httpx.Response(200, json=long if "via" in str(request.url) else DATA, request=request))
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.llm.on(r"pilot in fernley", {"intent": "add_stop", "query": "Pilot, Fernley NV"})
    agent_sandbox.geocode.update({"reno": [RENO], "pilot": [PILOT]})
    return agent_sandbox.provider(), agent.LlmAgentProvider()


def test_a_question_asked_by_one_worker_is_answered_on_another(agent_sandbox, fake_http, sqlite_store, monkeypatch):
    monkeypatch.setenv("SESSION_STORE", "db")
    worker_a, worker_b = _two_workers(agent_sandbox, fake_http)
    sid = "drv_1:dlg-9"
    first = run(worker_a.handle(AgentRequest(text="take me to Reno", session_id=sid, **SAC), "drv_1"))
    assert first.status == "route_set"
    reno = first.place
    asked = run(worker_a.handle(AgentRequest(text="stop at the Pilot in Fernley on the way", session_id=sid, active_place=reno, **SAC), "drv_1"))
    assert asked.status == "confirm" and "out of the way" in asked.reply
    assert sid not in agent._SESSIONS  # nothing in this process: the state is in the database
    agent._past_tasks.clear()
    answered = run(worker_b.handle(AgentRequest(text="yes", session_id=sid, active_place=reno, **SAC), "drv_1"))
    assert answered.status == "route_set" and answered.route.via == [[PILOT.lat, PILOT.lng]]
    # and the third worker sees the stop in the context
    ctx = worker_b._context(AgentRequest(text="hi", session_id=sid, active_place=reno, **SAC), run(session_store.get_store().load(sid)))
    assert "Stops on the way: Pilot Travel Center." in ctx


def test_memory_store_is_the_default_without_a_database(monkeypatch):
    monkeypatch.delenv("SESSION_STORE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert session_store.kind() == "memory"
    s = agent._session("drv_x:dlg")
    assert s is agent._SESSIONS["drv_x:dlg"] and isinstance(s, DialogState)


def test_nudges_pending_goes_through_the_store(api_client, sqlite_store, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    monkeypatch.setenv("SESSION_STORE", "db")
    import server

    async def offer(req, sess, key):
        sess["pending"] = {"d": {"intent": "go", "place": PILOT.model_dump()}, "text": "go Pilot", "kind": "nudge"}
        return []
    monkeypatch.setattr(server.nudges, "compute", offer)
    r = api_client.post("/api/nudges", json={**SAC, "session_id": "dlg-n"})
    assert r.status_code == 200
    me = api_client.get("/api/memory").status_code  # just to have the driver id resolved by a request
    assert me in (200, 503)
    con = sqlite3.connect(str(sqlite_store))
    rows = con.execute("SELECT session_id, state FROM dialog_sessions").fetchall()
    con.close()
    assert len(rows) == 1 and rows[0][0].endswith(":dlg-n") and '"kind":"nudge"' in rows[0][1]
    # DELETE /api/memory forgets the dialogs of this driver too
    api_client.delete("/api/memory")
    con = sqlite3.connect(str(sqlite_store))
    assert con.execute("SELECT count(*) FROM dialog_sessions").fetchone()[0] == 0
    con.close()


def test_a_learned_avoid_holds_with_the_database_store_on_any_worker(agent_sandbox, fake_http, sqlite_store, monkeypatch):
    """Review 2026-11-02, item 16: with SESSION_STORE=db the place choice read the learned facts from the
    in-process dialogs only. Three "not that one" on Pilot, "I'll stop suggesting Pilot", and Pilot again;
    a new dialog on another worker picked Pilot as well."""
    monkeypatch.setenv("SESSION_STORE", "db")
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    fake_http.add("cwwp2.dot.ca.gov", "/data/", json={"data": []})
    pilots = [Place(id=f"p{i}", name=f"Pilot Travel Center #{i}", category="truck_stop", lat=38.5, lng=-121.4,
                    address="1 Main St, Lodi, CA", distance_mi=2.0 + i) for i in range(4)]
    loves = Place(id="l", name="Love's Travel Stop", category="truck_stop", lat=38.5, lng=-121.4,
                  address="1 Main St, Lodi, CA", distance_mi=9.0)
    agent_sandbox.places_provider.places = pilots + [loves]
    agent_sandbox.llm.on(r"truck stop", {"intent": "find", "category": "truck_stop"})
    agent_sandbox.llm.on(r"not that one", {"intent": "pick", "place_index": None, "category": "truck_stop"})
    worker_a, worker_b = agent_sandbox.provider(), agent.LlmAgentProvider()
    res = run(worker_a.handle(AgentRequest(text="find a truck stop", session_id="drv_av:d1", **SAC), "drv_av"))
    for _ in range(3):
        res = run(worker_a.handle(AgentRequest(text="not that one", session_id="drv_av:d1", active_place=res.place, **SAC), "drv_av"))
    assert res.reply.endswith("Got it, I'll stop suggesting Pilot.") and res.place.id == "l"
    assert not agent._SESSIONS  # every dialog lives in the database
    agent._past_tasks.clear()
    fresh = run(worker_b.handle(AgentRequest(text="find a truck stop", session_id="drv_av:d2", **SAC), "drv_av"))
    assert fresh.place.id == "l"


def _concurrent_writes(store, sid):
    """A turn and a /nudges poll load the same dialog; the poll saves an offer while the turn is still running,
    then the turn saves its places. Both must be there afterwards (review 2026-11-02, item 21)."""
    async def scenario():
        seed = DialogState()
        seed["history"] = ["Driver: take me to Reno"]
        await store.save(sid, seed)
        turn, poll = await store.load(sid), await store.load(sid)
        turn["places"] = [RENO, PILOT]
        turn["history"].append("Copilot: Route to Reno is set")
        poll["pending"] = {"d": {"intent": "go", "place": PILOT.model_dump()}, "text": "go Pilot", "kind": "nudge"}
        poll["nudge_break_done"] = True
        await store.save(sid, poll)
        await store.save(sid, turn)
        # and a write that changed nothing leaves the row alone
        await store.save(sid, await store.load(sid))
        return await store.load(sid)
    return scenario


def _assert_both_kept(back):
    assert [p.id for p in back["places"]] == [RENO.id, PILOT.id] and back["history"][-1] == "Copilot: Route to Reno is set"
    assert back["nudge_break_done"] is True and back["pending"]["kind"] == "nudge"


def test_a_poll_and_a_turn_at_once_keep_each_others_changes(sqlite_store, monkeypatch):
    monkeypatch.setenv("SESSION_STORE", "db")
    store = session_store.DbSessionStore()
    _assert_both_kept(run(_concurrent_writes(store, "drv_c:dlg")()))


def test_an_offer_made_during_a_turn_survives_the_turn(agent_sandbox, fake_http, sqlite_store, monkeypatch):
    """The real interleaving: /nudges saves an offer while the agent turn waits on the places search; the turn
    saves after it and used to write the whole dialog it had loaded, offer gone."""
    import asyncio
    monkeypatch.setenv("SESSION_STORE", "db")
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    fake_http.add("cwwp2.dot.ca.gov", "/data/", json={"data": []})
    agent_sandbox.places_provider.places = [PILOT]
    agent_sandbox.llm.on(r"truck stop", {"intent": "find", "category": "truck_stop"})
    sid = "drv_d:dlg"
    store = session_store.get_store()

    async def scenario():
        gate = asyncio.Event()
        search = agent_sandbox.places_provider.search

        async def slow_search(category, lat, lng):
            await gate.wait()
            return await search(category, lat, lng)
        agent_sandbox.places_provider.search = slow_search
        turn = asyncio.create_task(agent_sandbox.provider().handle(
            AgentRequest(text="find a truck stop", session_id=sid, **SAC), "drv_d"))
        await asyncio.sleep(0.05)
        poll = await store.load(sid)
        poll["pending"] = {"d": {"intent": "go", "place": PILOT.model_dump()}, "text": "go Pilot", "kind": "nudge"}
        poll["nudge_parking_done"] = True
        await store.save(sid, poll)
        gate.set()
        res = await turn
        return res, await store.load(sid)
    res, back = run(scenario())
    assert res.status == "route_set" and [p.id for p in back["places"]] == [PILOT.id]
    assert back["pending"]["kind"] == "nudge" and back["nudge_parking_done"] is True


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL (a throwaway Postgres)")
def test_a_poll_and_a_turn_at_once_keep_each_others_changes_on_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("MEMORY_STORE", "pg")
    monkeypatch.setenv("SESSION_STORE", "db")
    store = session_store.DbSessionStore()
    sid = f"drv_pg:{uuid.uuid4().hex[:8]}"
    scenario = _concurrent_writes(store, sid)

    async def go():
        try:
            return await scenario()
        finally:
            await store.drop_prefix(sid)
            await db.close()
    _assert_both_kept(run(go()))
