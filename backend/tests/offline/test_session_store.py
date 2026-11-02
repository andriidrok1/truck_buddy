"""Dialog state that survives JSON and a session store that lets another process (or a restart) continue the
dialog, including a question asked on the previous turn."""
import copy
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
from conftest import fixture_json, run

from models import AgentRequest, Place, RouteInfo
from services import agent, session_store
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
    store._last_sweep = 0.0
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
