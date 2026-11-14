"""A driver who wants a shower gets a truck stop (QA 2026-11-11: a typed "душ" came back "не расслышал").

No live model: the prompt is checked for the rule, the canned LLM plays the model's misses (unclear, chat, a rest
area) and the guard behind the prompt has to turn each into a truck stop."""
import json
import re

import pytest
from conftest import fixture_json, run

import evals
from evals.dataset import read_jsonl
from models import AgentRequest, Place
from services import i18n
from services.agent import (PROMPT_CACHE_KEY, SHOWERS_LISTED, SHOWERS_UNKNOWN, SYSTEM_STATIC, asks_for_shower,
                            shower_note)

SAC = {"lat": 38.5816, "lng": -121.4944}
NEAR = Place(id="g-roadys", name="Roady's Truck Stop", category="truck_stop", lat=38.624, lng=-121.417, distance_mi=4.0)
LOVES = Place(id="atp-loves-1", name="Love's Travel Stop", category="truck_stop", lat=38.70, lng=-121.60,
              distance_mi=11.0, tags=["Truck diesel", "DEF", "Showers", "24/7"])
REST = Place(id="g-rest", name="Gold Run Rest Area", category="rest_area", lat=38.60, lng=-121.40, distance_mi=3.0)

RU = ["душ", "Душ", "где помыться", "где принять душ", "где можно принять душ", "хочу помыться", "нужен душ",
      "де помитися"]
EN = ["shower", "I need a shower", "where can I take a shower", "where can I wash up"]


@pytest.fixture(autouse=True)
def _real_catalog():
    i18n.catalog.cache_clear()
    yield
    i18n.catalog.cache_clear()


@pytest.fixture
def routes(fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_elkhorn"))
    return fake_http


def _turn(agent_sandbox, said: str, model: dict, lang: str, places=(NEAR, REST)):
    agent_sandbox.places_provider.places = list(places)
    agent_sandbox.llm.default = model
    return run(agent_sandbox.provider().handle(AgentRequest(text=said, session_id=f"s-{said}-{lang}", lang=lang, **SAC)))


# ---- the prompt ------------------------------------------------------------------------------

def test_prompt_files_a_shower_under_truck_stop_in_both_languages():
    truck_stop = re.search(r"truck_stop = (.*?);", SYSTEM_STATIC, re.S).group(1)
    for words in ('"душ"', '"где помыться"', '"где принять душ"', '"I need a shower"', "showers"):
        assert words in truck_stop, words
    unclear = re.search(r"- unclear: (.*?)\n(?:\S|$)", SYSTEM_STATIC, re.S).group(1)
    assert '"душ"' in unclear and "find truck_stop" in unclear  # one word that names a need is never unclear
    assert PROMPT_CACHE_KEY == "agent-v7"


def test_eval_set_has_the_shower_cases():
    hand = {c["id"]: c for c in read_jsonl(evals.HAND_FILE)}
    for cid in ("h_find_shower", "h_find_ru_dush", "h_find_ru_pomytsya", "h_find_ru_prinyat_dush", "h_find_en_wash_up"):
        assert hand[cid]["expected"] == {"intent": "find", "category": "truck_stop"}, cid
    assert hand["h_find_ru_motel_dush"]["expected"]["category"] == "hotel"


# ---- the words -------------------------------------------------------------------------------

@pytest.mark.parametrize("said", RU + EN)
def test_shower_words(said):
    assert asks_for_shower(said)


@pytest.mark.parametrize("said", ["помыть трак", "где помыть прицеп", "chance of rain showers", "душно в кабине",
                                  "find diesel", "найди дизель"])
def test_not_shower_words(said):
    assert not asks_for_shower(said)  # a truck wash, the weather, the heat


# ---- the guard behind the prompt -------------------------------------------------------------

@pytest.mark.parametrize("model", [{"intent": "unclear"}, {"intent": "chat", "reply": "Showers are at truck stops."},
                                   {"intent": "find", "category": "rest_area"}, {"intent": "find", "category": None}])
@pytest.mark.parametrize("said,lang", [(s, "ru") for s in RU] + [(s, "en") for s in EN])
def test_a_shower_ask_becomes_a_truck_stop(agent_sandbox, routes, said, lang, model):
    res = _turn(agent_sandbox, said, model, lang)
    assert res.status == "route_set" and res.category == "truck_stop", (said, model, res.reply)
    assert agent_sandbox.places_provider.calls[-1][0] == "truck_stop"


def test_the_qa_case_is_no_longer_not_understood(agent_sandbox, routes):
    res = _turn(agent_sandbox, "душ", {"intent": "unclear", "category": None, "query": None}, "ru")
    assert res.status == "route_set" and res.place.id == NEAR.id


def test_a_stop_with_showers_listed_goes_first_and_says_so(agent_sandbox, routes):
    res = _turn(agent_sandbox, "где принять душ", {"intent": "find", "category": "truck_stop"}, "ru",
                places=(NEAR, LOVES))
    assert res.place.id == LOVES.id and SHOWERS_LISTED.strip() in res.reply
    assert [p.id for p in res.options] == [NEAR.id]  # the closer one stays on the list


def test_no_shower_data_names_the_chains_not_the_place(agent_sandbox, routes):
    res = _turn(agent_sandbox, "I need a shower", {"intent": "find", "category": "truck_stop"}, "en")
    assert res.place.id == NEAR.id and SHOWERS_UNKNOWN.strip() in res.reply
    assert "Roady's" not in SHOWERS_UNKNOWN  # nothing is claimed about this stop


@pytest.mark.parametrize("said,model,category", [
    ("мотель с душем", {"intent": "find", "category": "hotel"}, "hotel"),
    ("a motel with a shower", {"intent": "find", "category": "hotel"}, "hotel"),
])
def test_a_bed_with_a_shower_stays_a_hotel(agent_sandbox, routes, said, model, category):
    hotel = Place(id="g-motel", name="Motel 6", category="hotel", lat=38.6, lng=-121.5, distance_mi=2.0)
    assert _turn(agent_sandbox, said, model, "ru", places=(NEAR, hotel)).category == category


def test_talk_about_the_showers_of_a_listed_place_stays_talk(agent_sandbox, routes):
    agent_sandbox.places_provider.places = [NEAR]
    agent_sandbox.llm.default = {"intent": "find", "category": "truck_stop"}
    p = agent_sandbox.provider()
    run(p.handle(AgentRequest(text="find a truck stop", session_id="talk", **SAC)))
    agent_sandbox.llm.default = {"intent": "chat", "reply": "I don't know about the showers there."}
    res = run(p.handle(AgentRequest(text="are the showers any good", session_id="talk", **SAC)))
    assert res.status == "chat"


def test_shower_note_only_for_a_shower_ask_at_a_truck_stop():
    assert shower_note("find diesel", NEAR) == ""
    assert shower_note("душ", REST) == "" and shower_note("душ", None) == ""
    assert shower_note("душ", NEAR) == SHOWERS_UNKNOWN and shower_note("душ", LOVES) == SHOWERS_LISTED


# ---- Russian -------------------------------------------------------------------------------

@pytest.mark.parametrize("en,ru", [
    (SHOWERS_LISTED.strip(), "Душ там указан."),
    (SHOWERS_UNKNOWN.strip(), "Есть ли там душ, точно не знаю, но на крупных сетях вроде Pilot, Love's и TA душ обычно есть."),
])
def test_the_shower_lines_are_in_the_catalog(en, ru):
    misses: list[str] = []
    assert i18n.translate(en, "ru", fallback=lambda s, c: misses.append(s) or None) == ru and not misses


def test_ru_json_is_built_from_the_lists():
    from pathlib import Path
    built = json.loads((Path(i18n.CATALOG_DIR) / "ru.json").read_text())
    assert any(t["en"] == SHOWERS_LISTED.strip() for t in built["templates"])


# ---- review of #179: only an ask for a place is a shower ask -----------------------------------

FAR_SHOWERS = Place(id="atp-ta-far", name="TA Truck Service", category="truck_stop", lat=39.0, lng=-121.0,
                    distance_mi=40.0, tags=["Showers"])
TRUCK_WASH = Place(id="g-blue-beacon", name="Blue Beacon Truck Wash", category="truck_wash", lat=38.6, lng=-121.45,
                   distance_mi=5.0)


@pytest.mark.parametrize("said,lang", [
    ("thanks, I'm gonna take a shower when I get there", "en"),
    ("any showers today?", "en"),
    ("I'll take a shower later", "en"),
    ("спасибо от души", "ru"),
    ("спасибо, потом помоюсь", "ru"),
    ("будет дождь с ливнями сегодня? душ с неба", "ru"),
])
def test_talk_about_a_shower_stays_chat(agent_sandbox, routes, said, lang):
    res = _turn(agent_sandbox, said, {"intent": "chat", "reply": "Sounds good."}, lang)
    assert res.status == "chat" and not agent_sandbox.places_provider.calls, (said, res.reply)


@pytest.mark.parametrize("said,lang", [
    ("where can I find a shower", "en"), ("I need a shower", "en"), ("find me a shower", "en"),
    ("nearest shower", "en"), ("any showers nearby?", "en"), ("где помыться", "ru"), ("где душ", "ru"),
    ("нужен душ", "ru"), ("душ рядом есть?", "ru"), ("душ", "ru"),
])
def test_a_chat_that_asks_for_a_shower_becomes_a_truck_stop(agent_sandbox, routes, said, lang):
    res = _turn(agent_sandbox, said, {"intent": "chat", "reply": "Truck stops have showers."}, lang)
    assert res.status == "route_set" and res.category == "truck_stop", (said, res.reply)


@pytest.mark.parametrize("said", ["души", "от души", "душевно", "душит", "душа болит", "в душе не знаю где",
                                  "по душе", "any showers today?", "rain showers tonight", "truck wash up ahead",
                                  "помыть трак", "погода: возможен душ"])
def test_soul_weather_and_truck_wash_are_not_shower_words(said):
    assert not asks_for_shower(said), said


@pytest.mark.parametrize("said", ["душем", "в душе", "душевая", "душевой", "ополоснуться", "помыться"])
def test_shower_forms(said):
    assert asks_for_shower(said), said


def test_from_the_soul_with_a_fuel_ask_takes_the_nearest_stop(agent_sandbox, routes):
    # "от души, есть рядом заправка?" picked a farther stop with showers (review of #179).
    res = _turn(agent_sandbox, "от души, есть рядом заправка?", {"intent": "find", "category": "truck_stop"}, "ru",
                places=(NEAR, LOVES))
    assert res.place.id == NEAR.id and SHOWERS_LISTED.strip() not in res.reply


@pytest.mark.parametrize("said,lang", [("где помыться", "ru"), ("хочу помыться", "ru"), ("I need a shower", "en")])
def test_a_personal_wash_filed_as_truck_wash_becomes_a_truck_stop(agent_sandbox, routes, said, lang, monkeypatch):
    monkeypatch.setenv("CATEGORIES_EXTRA", "truck_wash")
    res = _turn(agent_sandbox, said, {"intent": "find", "category": "truck_wash"}, lang, places=(NEAR, TRUCK_WASH))
    assert res.category == "truck_stop" and agent_sandbox.places_provider.calls[-1][0] == "truck_stop", res.reply


@pytest.mark.parametrize("said,lang", [("truck wash up ahead", "en"), ("помыть трак", "ru"),
                                       ("where can I wash the truck", "en")])
def test_a_truck_wash_stays_a_truck_wash(agent_sandbox, routes, said, lang, monkeypatch):
    monkeypatch.setenv("CATEGORIES_EXTRA", "truck_wash")
    _turn(agent_sandbox, said, {"intent": "find", "category": "truck_wash"}, lang, places=(NEAR, TRUCK_WASH))
    assert agent_sandbox.places_provider.calls[-1][0] == "truck_wash", said


def test_a_shower_stop_far_beyond_the_nearest_does_not_win(agent_sandbox, routes):
    res = _turn(agent_sandbox, "где принять душ", {"intent": "find", "category": "truck_stop"}, "ru",
                places=(NEAR, FAR_SHOWERS))
    assert res.place.id == NEAR.id and SHOWERS_UNKNOWN.strip() in res.reply  # 40 mi vs 4 mi: past the 15-mi cap


def test_the_shower_stop_gets_the_parking_warm_up(agent_sandbox, routes, monkeypatch):
    from services import agent
    warmed: list[str] = []

    monkeypatch.setattr(agent, "check_parking", lambda p, *a, **k: warmed.append(p.id))
    monkeypatch.setattr(agent, "_background", lambda _: None)
    res = _turn(agent_sandbox, "где принять душ", {"intent": "find", "category": "truck_stop"}, "ru",
                places=(NEAR, LOVES))
    assert res.place.id == LOVES.id and warmed[:1] == [LOVES.id]  # the same warm-up as the normal find path
