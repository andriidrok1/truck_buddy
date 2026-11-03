"""What goes into "likes" and "dislikes" (QA 2026-11-02, bug 5 / item 28): the stops the driver was sent to or
turned down were read back as liked chains, "remember I like Pilot and I hate tolls" changed only the profile,
and "what do you know about me" read raw notes ("Asked about Walmart sleeping; Wants CAT scales")."""
from datetime import datetime, timezone

from conftest import fixture_json, run

from models import AgentRequest, Place, TruckProfile
from services import agent, memory, profile_learn

SAC = {"lat": 38.5816, "lng": -121.4944}


def _stop(id, name, mi):
    return Place(id=id, name=f"{name}, Sacramento, CA", category="truck_stop", lat=38.6, lng=-121.5,
                 address="1 El Centro Rd, Sacramento, CA", distance_mi=mi)


FORTYNINER = _stop("g-49er", "Sacramento 49er Travel Plaza", 2.0)
WESTSAC = _stop("g-westsac", "West Sac Truck Stop", 4.0)
PILOT = _stop("g-pilot", "Pilot Travel Center", 6.0)


def _fake_store(monkeypatch, stored=None):
    store = dict(stored or {})

    async def get_facts(driver):
        return store.get(driver)

    async def set_facts(driver, facts, turns_seen):
        store[driver] = {"facts": facts, "turns_seen": turns_seen, "updated_at": datetime.now(timezone.utc)}

    monkeypatch.setattr(memory, "get_facts", get_facts)
    monkeypatch.setattr(memory, "set_facts", set_facts)
    return store


def test_a_summary_keeps_only_the_likes_the_driver_said(fake_llm):
    # the model listed where the driver was sent as "liked chains", and requests as notes
    fake_llm.on(r"^Driver said", '{"chains_liked": ["Pilot", "Sacramento 49er Travel Plaza", "West Sac Truck Stop"], '
                                 '"notes": ["Asked about Walmart sleeping", "Wants CAT scales", "you haul reefer"]}')
    f = run(profile_learn.summarize(["take me to the 49er", "find a truck stop", "I really like Pilot",
                                     "is there a CAT scale", "I haul reefer"]))
    assert f["chains_liked"] == ["Pilot"] and f["places_liked"] == []
    assert f["notes"] == ["you haul reefer"]


def test_chains_and_particular_places_are_kept_apart(fake_llm):
    fake_llm.on(r"^Driver said", '{"chains_liked": ["Love\'s Travel Stops", "the 49er"], '
                                 '"chains_avoided": ["TA", "Pilot Travel Center, Fernley"]}')
    f = run(profile_learn.summarize(["I love Love's", "I like the 49er", "I hate TA and that Pilot in Fernley"]))
    assert f["chains_liked"] == ["Love's"] and f["places_liked"] == ["the 49er"]
    assert f["chains_avoided"] == ["TA"] and f["places_avoided"] == ["Pilot Travel Center, Fernley"]


def test_old_rows_lose_the_stops_that_were_never_liked():
    old = {"chains_liked": ["West Sac Truck Stop", "Sacramento 49er Travel Plaza", "Pilot"], "chains_avoided": [],
           "avoids": [], "notes": ["Asked about Walmart sleeping", "Wants CAT scales"]}
    f = profile_learn.tidy(old)
    assert f["chains_liked"] == ["Pilot"] and f["places_liked"] == [] and f["notes"] == []


def test_a_turned_down_place_is_a_dislike_and_comes_last_next_time(agent_sandbox, fake_http, monkeypatch):
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    fake_http.add("cwwp2.dot.ca.gov", "/data/", json={"data": []})
    store = _fake_store(monkeypatch)
    agent_sandbox.places_provider.places = [FORTYNINER, WESTSAC, PILOT]
    agent_sandbox.llm.on(r"truck stop", {"intent": "find", "category": "truck_stop"})
    agent_sandbox.llm.on(r"not that one", {"intent": "pick", "place_index": None, "category": "truck_stop"})
    prov = agent_sandbox.provider()
    first = run(prov.handle(AgentRequest(text="find me a truck stop", session_id="drv5:a", **SAC), "drv5"))
    assert first.place.id == FORTYNINER.id
    run(prov.handle(AgentRequest(text="not that one", session_id="drv5:a", active_place=first.place, **SAC), "drv5"))
    facts = store["drv5"]["facts"]
    assert facts["places_avoided"] == ["Sacramento 49er Travel Plaza"]
    assert not facts.get("chains_liked") and not facts.get("places_liked")
    # a new dialog: the 49er is still the nearest, and it is not the pick
    agent._past_tasks.clear()
    again = run(prov.handle(AgentRequest(text="find me a truck stop", session_id="drv5:b", **SAC), "drv5"))
    assert again.place.id == WESTSAC.id


def test_remember_i_like_pilot_and_i_hate_tolls_goes_to_memory_and_the_profile(agent_sandbox, monkeypatch):
    store = _fake_store(monkeypatch)
    # what the model answered on the stand: a control, so only the profile changed
    agent_sandbox.llm.on(r"remember", {"intent": "control", "actions": [
        {"type": "add_favorite_chain", "value": "Pilot"}, {"type": "avoid_tolls", "value": True}]})
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="Remember I like Pilot and I hate tolls", session_id="drv6:a", **SAC), "drv6"))
    assert res.reply == "Got it, I'll remember you like Pilot and you hate tolls."
    assert {(a.type, a.value) for a in res.actions} == {("add_favorite_chain", "Pilot"), ("avoid_tolls", True)}
    facts = store["drv6"]["facts"]
    assert facts["chains_liked"] == ["Pilot"] and facts["avoids"] == ["tolls"]
    # the model may call it chat or memory: the same
    agent_sandbox.llm.rules.clear()
    agent_sandbox.llm.on(r"remember", {"intent": "chat", "reply": "Sure thing."})
    res = run(prov.handle(AgentRequest(text="remember that I don't like the 49er", session_id="drv6:a", **SAC), "drv6"))
    assert res.reply == "Got it, I'll remember you don't like the 49er."
    assert store["drv6"]["facts"]["places_avoided"] == ["the 49er"] and store["drv6"]["facts"]["chains_liked"] == ["Pilot"]


def test_what_i_know_is_said_in_sentences(agent_sandbox):
    agent_sandbox.llm.on(r"know about me", {"intent": "memory", "query": "show"})
    prov = agent_sandbox.provider()
    run(prov.handle(AgentRequest(text="hi", session_id="kn", **SAC), None))
    agent._SESSIONS["kn"]["facts"] = {
        "chains_liked": ["Pilot", "Love's"], "chains_avoided": ["TA"], "places_liked": [],
        "places_avoided": ["Sacramento 49er Travel Plaza"], "avoids": ["tolls"], "stops_for_night_at": None,
        "style": None, "notes": ["Asked about Walmart sleeping", "Wants CAT scales", "you haul reefer"]}
    truck = TruckProfile(favorite_chains=["Pilot"], avoid_tolls=True)
    res = run(prov.handle(AgentRequest(text="what do you know about me", session_id="kn", truck_profile=truck, **SAC), None))
    assert res.reply == ("Here's what I know. You like Love's. You'd rather skip TA and Sacramento 49er Travel Plaza. "
                         "You haul reefer. From your profile: you like Pilot, you avoid toll roads.")
