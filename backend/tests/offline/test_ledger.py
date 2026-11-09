"""The owner-operator's ledger (plan 2026-11-09, second brain step 1): a rate said by voice, read back and saved on
"yes" with the route's miles; "how much did I make" summed by SQL and worded by a template; fix and delete the last
load; only for an owner-operator; nothing of it reaches a fleet; "delete my data" takes it."""
import copy
import json
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from conftest import fixture_json, run

from models import AgentRequest, Place, RouteInfo, TruckProfile
from services import agent, db, fuel_index, ledger, tenancy, trip_events
from services.agent.phrases import ledger_phrase


def _with_states(data):
    """The Sacramento->Reno fixture with stateCode spans (60 % CA, the rest NV), as test_route_cost builds it."""
    import flexpolyline
    data = copy.deepcopy(data)
    for r in data["routes"]:
        for s in r["sections"]:
            n = len(flexpolyline.decode(s["polyline"]))
            s["spans"] = [{"offset": 0, "stateCode": "CA"}, {"offset": int(n * 0.6), "stateCode": "NV"}]
    return data


DATA = _with_states(fixture_json("here/route_sac_reno"))
SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)
OWNER = TruckProfile(operator="owner")


@pytest.fixture(autouse=True)
def _no_state_lookup(monkeypatch):
    """The time zone comes from the state the truck is in (a HERE call): never in these tests."""
    async def nevada(lat, lng):
        return "NV"
    monkeypatch.setattr(fuel_index, "state_code", nevada)


# ---- amounts ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("said,want", [
    ("this load pays twenty four hundred", 2400), ("$2,400", 2400), ("2400", 2400), ("2,400 dollars", 2400),
    ("2.4k", 2400), ("2.4 K", 2400), ("twenty-four hundred", 2400), ("two thousand four hundred", 2400),
    ("24 hundred", 2400), ("2 thousand 400", 2400), ("twenty four hundred fifty", 2450),
    ("twenty four hundred and fifty", 2450), ("one thousand eight hundred fifty", 1850), ("a thousand bucks", 1000),
    ("two thousand and fifty", 2050), ("fifty five hundred", 5500), ("2.5 grand", 2500), ("$2,412.50", 2412.5),
    ("the rate is 3k", 3000), ("it paid me 1,850 dollars", 1850), ("load on I-80 pays 2400", 2400),
    ("the 2nd load pays 2200", 2200), ("load to 1200 Main St pays 2400", 2400), ("$1,200 to 500 Main St", 1200),
])
def test_amounts_drivers_say(said, want):
    assert ledger.parse_amount(said) == want


@pytest.mark.parametrize("said", [
    "twenty four",  # 24 or 2,400
    "eighteen fifty",  # 1,850 or 18 and 50
    "load 1200 to 500",  # two amounts, nothing tells the rate
    "pays well", "", "a million dollars",
])
def test_amounts_that_read_two_ways_or_none(said):
    assert ledger.parse_amount(said) is None


def test_the_models_amount_only_when_the_words_have_none():
    assert ledger.amount_of("груз платит две четыреста", {"amount": 2400}) == 2400
    assert ledger.amount_of("pays 2600", {"amount": 2400}) == 2600  # the words win
    assert ledger.amount_of("pays a lot", {"amount": 24}) is None and ledger.amount_of("x", {"amount": "?"}) is None


def test_fast_phrases():
    assert ledger_phrase("This load pays twenty four hundred") == {"intent": "ledger", "op": "add", "amount": 2400}
    assert ledger_phrase("the rate is $1,850 on this run")["amount"] == 1850
    assert ledger_phrase("how much did I make last week") == {"intent": "ledger", "op": "totals", "period": "last week"}
    assert ledger_phrase("how much have I made") == {"intent": "ledger", "op": "totals", "period": "this week"}
    assert ledger_phrase("delete the last load") == {"intent": "ledger", "op": "delete"}
    # no amount, or no load: the model decides
    assert ledger_phrase("this load pays twenty four") is None and ledger_phrase("how much is diesel") is None
    assert ledger_phrase("take me to 2400 Main St") is None
    assert ledger_phrase("the rate is 120 at that hotel") is None


# ---- periods ---------------------------------------------------------------------------------------------------

def test_periods_from_words():
    assert ledger.period_of("how much did I make today") == "today"
    assert ledger.period_of("and yesterday?") == "yesterday"
    assert ledger.period_of("what did I gross last month") == "last month"
    assert ledger.period_of("this month so far") == "this month"
    assert ledger.period_of("how much did I make") == "this week"
    assert ledger.period_of("how much", "last week") == "last week"


def test_weeks_start_monday_in_the_drivers_zone():
    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 11, 11, 6, 30, tzinfo=timezone.utc)  # Tuesday 22:30 in Los Angeles
    start, end = ledger.window("this week", la, now)
    assert start == datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc) and end == datetime(2026, 11, 16, 8, tzinfo=timezone.utc)
    start, _ = ledger.window("today", la, now)
    assert start == datetime(2026, 11, 10, 8, tzinfo=timezone.utc)  # still Tuesday there, Wednesday in UTC
    start, end = ledger.window("last month", la, now)
    assert start == datetime(2026, 10, 1, 7, tzinfo=timezone.utc) and end == datetime(2026, 11, 1, 7, tzinfo=timezone.utc)


def test_a_week_across_the_dst_change_starts_at_local_midnight():
    chi = ZoneInfo("America/Chicago")
    start, end = ledger.window("this week", chi, datetime(2026, 11, 1, 18, tzinfo=timezone.utc))  # Sunday, after the change
    assert start == datetime(2026, 10, 26, 5, tzinfo=timezone.utc)  # CDT
    assert end == datetime(2026, 11, 2, 6, tzinfo=timezone.utc)  # CST


def test_zone_from_the_phone_then_the_state_then_chicago(monkeypatch):
    assert str(run(ledger.zone_at(39.5, -119.8, "America/Denver"))) == "America/Denver"
    assert str(run(ledger.zone_at(39.5, -119.8, "Mars/Base"))) == "America/Los_Angeles"  # NV, from the fixture

    async def nowhere(lat, lng):
        return None
    monkeypatch.setattr(fuel_index, "state_code", nowhere)
    assert str(run(ledger.zone_at(39.5, -119.8))) == "America/Chicago"


# ---- the store -------------------------------------------------------------------------------------------------

def _snap(**kw):
    return ledger.Snapshot(**{"dest_name": "Reno", "dest_place_id": "here-reno", "miles": 132.0,
                              "state_miles": {"CA": 82.04, "NV": 49.96}, "fuel_usd": 110.0, "tolls_usd": 0.0, **kw})


def test_add_last_fix_delete_and_totals(sqlite_store):
    mon = datetime(2026, 11, 9, 15, tzinfo=timezone.utc)
    a = run(ledger.add("d1", 2400, _snap(trip_id="t1"), at=mon))
    b = run(ledger.add("d1", 3000, _snap(dest_name="Salt Lake City", miles=518.0, fuel_usd=420.0, tolls_usd=46.0),
                       at=mon.replace(day=10)))
    run(ledger.add("d1", 999, _snap(), at=datetime(2026, 11, 2, 15, tzinfo=timezone.utc)))  # the week before
    run(ledger.add("other", 5000, _snap(), at=mon))  # another driver's
    last = run(ledger.last("d1"))
    assert last["id"] == b and last["rate_usd"] == 3000 and last["dest_name"] == "Salt Lake City"
    first = run(ledger.loads("d1", mon, mon.replace(day=10)))[0]
    assert first["state_miles"] == {"CA": 82.0, "NV": 50.0} and first["trip_id"] == "t1" and first["id"] == a
    start, end = ledger.window("this week", ZoneInfo("America/Chicago"), mon.replace(day=11))
    t = run(ledger.totals("d1", start, end))
    assert (t.n, t.gross, t.fuel, t.tolls, t.miles) == (2, 5400, 530, 46, 650)
    assert run(ledger.set_rate("d1", b, 3100)) and run(ledger.last("d1"))["rate_usd"] == 3100
    assert not run(ledger.set_rate("other", b, 1))  # by id and driver: never someone else's row
    assert run(ledger.delete("d1", b)) and run(ledger.last("d1"))["id"] == a
    assert not run(ledger.delete("d1", b))


def test_a_load_without_a_route_is_filled_later(sqlite_store):
    i = run(ledger.add("d1", 2400, ledger.Snapshot()))
    assert run(ledger.fill("d1", i, _snap(trip_id="t9")))
    row = run(ledger.last("d1"))
    assert row["miles"] == 132.0 and row["dest_name"] == "Reno" and row["trip_id"] == "t9"
    run(ledger.fill("d1", i, _snap(dest_name="Elko", miles=1.0)))
    assert run(ledger.last("d1"))["dest_name"] == "Reno"  # what it has stays


def test_arrival_at_the_trips_destination_marks_its_loads_delivered(sqlite_store):
    run(ledger.add("d1", 2400, _snap(trip_id="t1")))
    stop = {"kind": "arrived", "trip_id": "t1", "data": {"place_id": "p", "kind": "stop", "category": "truck_stop"}}
    run(trip_events.add("d1", [stop]))
    assert run(ledger.last("d1"))["delivered_at"] is None  # a stop on the way is not the delivery
    dest = {"kind": "arrived", "trip_id": "t1", "data": {"place_id": "here-reno", "kind": "destination"}}
    run(trip_events.add("d1", [dest]))
    assert isinstance(run(ledger.last("d1"))["delivered_at"], datetime)


def test_totals_are_spoken_from_python_not_the_model():
    rows = [{"rate_usd": 2400, "fuel_usd": 400, "tolls_usd": 0, "miles": 800},
            {"rate_usd": 2450, "fuel_usd": 390, "tolls_usd": 46, "miles": 790},
            {"rate_usd": 2000, "fuel_usd": 330, "tolls_usd": 0, "miles": 768}]
    said = ledger.reply_totals(ledger.sum_up(rows), "this week")
    assert said == "This week: 3 loads, $6,850. Diesel about $1,120, tolls $46, so about $5,680 net, $2.41 a mile."
    no_fuel = [dict(r, fuel_usd=None) for r in rows]
    assert ledger.reply_totals(ledger.sum_up(no_fuel), "today") == "Today: 3 loads, $6,850. That's $2.91 a mile before fuel."
    one_bare = rows[:1] + [{"rate_usd": 1000, "fuel_usd": None, "tolls_usd": None, "miles": None}]
    assert ledger.reply_totals(ledger.sum_up(one_bare), "last week") == "Last week: 2 loads, $3,400. 1 of them has no miles yet."
    assert ledger.reply_totals(ledger.sum_up([]), "last week") == ledger.NONE_IN.format(period="last week")


# ---- the voice turn --------------------------------------------------------------------------------------------

def _req(text, sid, truck=OWNER, **kw):
    return AgentRequest(text=text, session_id=sid, truck_profile=truck, **SAC, **kw)


def _turn(prov, text, sid, truck=OWNER, driver="drv1", **kw):
    return run(prov.handle(_req(text, sid, truck, **kw), driver))


def _rows(path):
    con = sqlite3.connect(str(path))
    try:
        return con.execute("SELECT driver, rate_usd, dest_name, miles, state_miles, trip_id FROM driver_loads").fetchall()
    finally:
        con.close()


@pytest.fixture
def prov(agent_sandbox, fake_http, sqlite_store):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.llm.on(r"take me to Reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.geocode["reno"] = [RENO]
    return agent_sandbox.provider()


def test_a_rate_on_a_route_is_read_back_and_saved_on_yes(prov, agent_sandbox, sqlite_store):
    assert _turn(prov, "take me to Reno", "L1").status == "route_set"
    calls = len(agent_sandbox.llm.calls)
    res = _turn(prov, "This load pays twenty four hundred", "L1", trip_id="trip-7", active_place=RENO)
    assert res.status == "confirm"
    assert res.reply.startswith("$2,400 for the load to Reno, ") and res.reply.endswith(" miles. Save it?")
    assert len(agent_sandbox.llm.calls) == calls  # the fast path: no model, nothing to mishear
    assert _rows(sqlite_store) == []
    res = _turn(prov, "yes, save it", "L1", trip_id="trip-7", active_place=RENO)
    assert res.status == "chat" and res.reply.startswith("Saved. That's $") and res.reply.endswith(" a mile before fuel.")
    [(driver, rate, dest, miles, states, trip)] = _rows(sqlite_store)
    assert (driver, rate, dest, trip) == ("drv1", 2400, "Reno", "trip-7") and miles > 100
    assert set(json.loads(states)) == {"CA", "NV"}  # through the dialog's JSON copy of the route
    res = _turn(prov, "how much did I make this week", "L1")
    assert res.reply.startswith("This week: 1 load, $2,400.") and len(agent_sandbox.llm.calls) == calls


def test_no_keeps_nothing(prov, sqlite_store):
    assert _turn(prov, "the rate is 1850 on this load", "L2").status == "confirm"
    res = _turn(prov, "no", "L2")
    assert res.reply == ledger.NOT_SAVED and _rows(sqlite_store) == []


def test_a_company_driver_gets_no_ledger(prov, sqlite_store):
    for truck in (TruckProfile(operator="company"), TruckProfile()):
        res = _turn(prov, "this load pays 2400", "L3", truck)
        assert res.status == "chat" and res.reply == ledger.OWNER_ONLY
        assert "pending" not in agent._SESSIONS["L3"]
    assert _turn(prov, "yes", "L3", TruckProfile(operator="company")).reply != ledger.saved(2400, ledger.Snapshot())
    assert _rows(sqlite_store) == []


def test_totals_with_nothing_saved(prov):
    res = _turn(prov, "how much did I make today", "L4")
    assert res.reply == "No loads saved today. Tell me what a load pays and I'll keep it."


def test_without_a_route_the_miles_come_with_the_next_destination(prov, sqlite_store, monkeypatch):
    res = _turn(prov, "this load pays 2400", "L5")
    assert res.reply == "$2,400 for the load. Save it?"
    assert _turn(prov, "yeah", "L5").reply == ledger.SAVED_NO_MILES
    assert agent._SESSIONS["L5"].get("ledger_fill") == 1
    # a truck stop is not where the load goes; the next destination routed fills the row in the background
    req = _req("take me to Reno", "L5")
    r = RouteInfo(geometry=[[38.58, -121.49], [39.53, -119.81]], distance_m=212000, duration_s=9000, distance_mi=131.7,
                  duration_min=150, summary="2 h 30 min · 132 mi", source="here", state_mi={"CA": 82.0, "NV": 49.7})
    assert prov._ledger_fill(req, Place(**{**RENO.model_dump(), "category": "truck_stop"}), r) is None  # not a load's end
    later = []
    monkeypatch.setattr(agent, "_background", later.append)
    assert _turn(prov, "take me to Reno", "L5").status == "route_set"
    for coro in later:
        run(coro) if coro.__qualname__ == "_quietly" else coro.close()
    [(_, _, dest, miles, states, _)] = _rows(sqlite_store)
    assert dest == "Reno" and miles > 100 and set(json.loads(states)) == {"CA", "NV"}
    assert "ledger_fill" not in agent._SESSIONS["L5"] and prov._ledger_fill(req, RENO, r) is None  # once


def test_fix_and_delete_the_last_load(prov, agent_sandbox, sqlite_store):
    assert _turn(prov, "take me to Reno", "L6").status == "route_set"
    _turn(prov, "this load pays 2400", "L6", active_place=RENO)
    _turn(prov, "yes", "L6", active_place=RENO)
    agent_sandbox.llm.on(r"that was wrong", {"intent": "ledger", "op": "fix", "amount": 2600})
    res = _turn(prov, "that was wrong, it's twenty six hundred", "L6")
    assert res.status == "confirm" and res.reply == "Change the last load to Reno from $2,400 to $2,600?"
    assert _turn(prov, "yes", "L6").reply == "Fixed. The load to Reno pays $2,600 now."
    assert _rows(sqlite_store)[0][1] == 2600
    res = _turn(prov, "delete the last load", "L6")
    assert res.reply == "Delete the last load, $2,600 to Reno?"
    assert _turn(prov, "no", "L6").reply == ledger.KEPT and len(_rows(sqlite_store)) == 1
    _turn(prov, "delete the last load", "L6")
    assert _turn(prov, "yes, delete it", "L6").reply == "Deleted the $2,600 load to Reno."
    assert _rows(sqlite_store) == []
    assert _turn(prov, "delete the last load", "L6").reply == ledger.NOTHING_SAVED


def test_store_off_or_flag_off(agent_sandbox, monkeypatch):
    prov = agent_sandbox.provider()  # MEMORY_STORE=off
    _turn(prov, "this load pays 2400", "L7")
    assert _turn(prov, "yes", "L7").reply == ledger.DOWN
    monkeypatch.setenv("LEDGER", "0")
    agent_sandbox.llm.on(r"pays 2400", {"intent": "chat", "reply": "Nice load."})
    assert _turn(prov, "this load pays 2400", "L8").reply == "Nice load."  # no fast path: the model's call


def test_no_ledger_number_goes_into_a_trip_event(prov, sqlite_store, monkeypatch):
    """timing notes become trip events (trip_events.from_turn), and their numbers reach the fleet."""
    from services import timing
    timing.begin()
    _turn(prov, "this load pays 2400", "L9")
    _turn(prov, "yes", "L9")
    notes = timing.notes()
    assert notes.get("ledger_add") == "accepted" and "ledger_add" not in trip_events._NOTE_KINDS
    assert not any("2400" in str(v) for v in notes.values())


# ---- privacy over HTTP -----------------------------------------------------------------------------------------

@pytest.fixture
def client(sqlite_store, monkeypatch):
    from fastapi.testclient import TestClient
    import server
    from services import auth
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    tenancy.reset_caches()
    auth._signups.clear()
    with TestClient(server.app) as c:
        yield c


def _device(c, code=None) -> dict:
    h = {"Authorization": f"Bearer {c.post('/api/auth/device').json()['access_token']}"}
    if code:
        h = {"Authorization": f"Bearer {c.post('/api/auth/fleet-code', json={'code': code}, headers=h).json()['access_token']}"}
    return h


def test_a_fleet_never_sees_the_ledger_and_delete_my_data_takes_it(client, agent_sandbox, sqlite_store):
    """An owner-operator leased onto a fleet: the fleet's dashboard routes have the trip, never the money."""
    acme = run(tenancy.create_org("Acme"))
    unit = run(tenancy.create_vehicle(acme, "Unit 1"))
    owner = _device(client, run(tenancy.create_fleet_code(acme, None, "owner")))
    drv = _device(client, run(tenancy.create_fleet_code(acme, unit)))
    body = {**SAC, "truck_profile": OWNER.model_dump(), "trip_id": "trip-L"}
    r = client.post("/api/agent", json={**body, "text": "this load pays twenty four hundred"}, headers=drv)
    assert r.status_code == 200 and r.json()["status"] == "confirm", r.text
    assert client.post("/api/agent", json={**body, "text": "yes"}, headers=drv).json()["reply"].startswith("Saved")
    ev = [{"kind": "route_started", "trip_id": "trip-L", "data": {"lat": 38.6, "lng": -121.4}},
          {"kind": "arrived", "trip_id": "trip-L", "data": {"place_id": "here-reno", "kind": "destination",
                                                            "lat": 39.5, "lng": -119.8}}]
    assert client.post("/api/trips/events", json=ev, headers=drv).status_code == 200
    client.get("/api/trips/trip-L/events", headers=drv)  # settles the driver's background saves
    rows = run(db._sq_run(lambda con: con.execute("SELECT delivered_at, rate_usd FROM driver_loads").fetchall()))
    assert len(rows) == 1 and rows[0][0] is not None and rows[0][1] == 2400
    paths = ("/api/fleet/vehicles", f"/api/fleet/vehicles/{unit}/events", "/api/fleet/summary", "/api/fleet/drivers")
    seen = [client.get(p, headers=owner) for p in paths]
    assert all(s.status_code == 200 for s in seen), [s.text for s in seen]
    assert len(seen[1].json()["events"]) >= 2  # the trip is there
    for s in seen:
        assert "2400" not in s.text and "2,400" not in s.text and "rate" not in s.text
    data = run(db._sq_run(lambda con: con.execute("SELECT data FROM trip_events").fetchall()))
    assert not any("2400" in d[0] for d in data)
    assert client.delete("/api/memory", headers=drv).status_code == 200
    assert run(db._sq_run(lambda con: con.execute("SELECT count(*) FROM driver_loads").fetchone()))[0] == 0


def test_the_routes_state_miles_survive_the_dialogs_json():
    """The dialog keeps the route as JSON (session_store): the private share the router set used to be lost there."""
    from services.dialog_state import DialogState
    r = RouteInfo(geometry=[[38.58, -121.49]], distance_m=1, duration_s=1, distance_mi=132.0, duration_min=1, summary="",
                  source="here")
    r.set_state_miles({"CA": 132.0, "NV": 80.5})
    st = DialogState(alternatives={"place": "here-reno", "current": r, "routes": []}, ledger_fill=3)
    back = DialogState.from_json(st.to_json())
    assert back["alternatives"]["current"].state_mi == pytest.approx({"CA": 82.02, "NV": 50.02}, abs=0.01)
    assert back["alternatives"]["current"].state_share["CA"] == pytest.approx(132 / 212.5)
    assert back.get("ledger_fill") == 3


def test_migration_0011_makes_the_table_without_an_org(sqlite_store):
    run(db.migrate())
    cols = run(db._sq_run(lambda con: [r[1] for r in con.execute("PRAGMA table_info(driver_loads)")]))
    assert {"driver", "trip_id", "rate_usd", "miles", "state_miles", "fuel_usd", "tolls_usd", "delivered_at"} <= set(cols)
    assert "org_id" not in cols and "vehicle_id" not in cols
