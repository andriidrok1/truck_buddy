"""The Russian QA pass of 2026-11-09 (vault: «TruckBuddy — QA русской версии»), server side: the history shows the
answers as the driver saw them (bug 1), the driver's own spelling of a town and no ", United States" (bug 8), the
S17 answers by template with no model call, the model fallback's "ты" and "трак" (bug 9), route incidents, NWS
events and chain controls (S10), the backup-route notice (S18), HERE maneuvers (S19) and the breakdown card (S7)."""
import re

import pytest
from conftest import fixture_json, run

from models import (AgentRequest, CallCard, ChainControl, Incident, Maneuver, Place, RouteInfo, TruckProfile,
                    WeatherAlert)
from services import agent, i18n, memory, routing
from services.geo import without_country

# The real store functions: agent_sandbox swaps them for no-ops.
_ADD_LATER, _RECENT = memory.add_later, memory.recent
SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-town-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)


@pytest.fixture(autouse=True)
def _real_catalog():
    i18n.catalog.cache_clear()  # test_i18n swaps in a small catalog of its own
    yield
    i18n.catalog.cache_clear()


def _ru(text: str) -> tuple[str, list[str]]:
    misses: list[str] = []
    return i18n.translate(text, "ru", fallback=lambda s, c: misses.append(s) or None), misses


# ---- bug 1: the history screen --------------------------------------------------------------------------------

@pytest.fixture
def remembering(agent_sandbox, sqlite_store, monkeypatch):
    monkeypatch.setattr(memory, "add_later", _ADD_LATER)
    monkeypatch.setattr(memory, "recent", _RECENT)
    agent_sandbox.llm.default = {"intent": "chat", "reply": "I can't tell where we are right now."}
    return agent_sandbox


def _turn(sandbox, code: str, text: str, sid: str):
    async def go():
        i18n.set_lang(code)  # inside the task: the test thread's context stays English
        await sandbox.provider().handle(AgentRequest(text=text, session_id=sid, lang=code, **SAC), "drv")
        return await memory.recent("drv", 10)
    return run(go())


def test_history_keeps_the_answer_the_driver_saw(remembering):
    turns = _turn(remembering, "ru", "где мы", "h-ru")
    assert [(t["role"], t["text"]) for t in turns] == [("driver", "где мы"), ("copilot", "Сейчас не могу понять, где мы.")]
    # the model's own context stays English: it reads the dialog history, not the stored translation
    assert agent._SESSIONS["h-ru"]["history"][-1] == "Copilot: I can't tell where we are right now."
    assert not any(c[0].startswith("Translate") for c in remembering.llm.calls)  # the catalog had it: no model call


def test_history_in_english_is_unchanged(remembering):
    turns = _turn(remembering, "en", "where are we", "h-en")
    assert [t["text"] for t in turns] == ["where are we", "I can't tell where we are right now."]


def test_history_over_the_api_matches_the_reply(api_client, remembering, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    r = api_client.post("/api/agent", json={"text": "где мы", **SAC}, headers={"X-Lang": "ru"})
    assert r.status_code == 200, r.text
    shown = r.json()["reply"]
    assert shown == "Сейчас не могу понять, где мы."
    turns = api_client.get("/api/memory").json()["turns"]
    assert turns[-1] == {**turns[-1], "role": "copilot", "text": shown}


# ---- bug 8: the place as the driver wrote it, no ", United States" ---------------------------------------------

def test_country_tail_is_cut():
    assert without_country("Reno, NV, United States") == "Reno, NV"
    assert without_country("2100 Garson Rd, Verdi, NV 89439, USA") == "2100 Garson Rd, Verdi, NV 89439"
    assert without_country("United States") == "" and without_country(None) is None
    assert without_country("United States Post Office, Reno, NV") == "United States Post Office, Reno, NV"


def test_geocoder_place_has_no_country(fake_http):
    from services.geocode import geocode
    item = {"title": "Reno, NV, United States", "resultType": "locality", "id": "x",
            "address": {"label": "Reno, NV, United States"}, "position": {"lat": 39.53, "lng": -119.81},
            "scoring": {"queryScore": 1.0}}
    fake_http.add("geocode.search.hereapi.com", "/v1/geocode", json={"items": [item]})
    fake_http.add("discover.search.hereapi.com", "/v1/discover", json={"items": []})
    found = run(geocode("Reno, NV", **SAC))
    assert found[0].name == "Reno, NV" and found[0].address is None


def test_driver_spelling_of_towns():
    said = "маршрут до Рино"
    assert i18n.driver_names("Маршрут до Reno готов, 2 часа 48 минут.", said) == "Маршрут до Рино готов, 2 часа 48 минут."
    assert i18n.driver_names("Reno в 23:48: 44 градуса.", "погода в Рино") == "Рино в 23:48: 44 градуса."
    assert i18n.driver_names("Едем в Sacramento.", "поехали в сакраменто") == "Едем в Сакраменто."
    assert i18n.driver_names("Едем в Los Angeles.", "в Лос-Анджелес") == "Едем в Лос-Анджелес."
    assert i18n.driver_names("Едем в Dallas.", "до Далласа") == "Едем в Даллас."  # the case ending comes off
    # a business name stays whole unless the driver wrote all of it; an unrelated name stays Latin
    assert i18n.driver_names("Pilot Travel Center, 3 мили.", "найди пилот") == "Pilot Travel Center, 3 мили."
    assert i18n.driver_names("Едем в Fresno.", "маршрут до Рино") == "Едем в Fresno."
    assert i18n.driver_names("Route to Reno is set.", "route to Reno") == "Route to Reno is set."


def test_russian_route_reply_uses_the_drivers_spelling(api_client, agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    fake_http.add("cwwp2.dot.ca.gov", "/data/", json={"data": []})
    agent_sandbox.llm.on(r"рино", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.geocode["reno"] = [RENO]
    r = api_client.post("/api/agent", json={"text": "маршрут до Рино", **SAC}, headers={"X-Lang": "ru"})
    assert r.status_code == 200, r.text
    reply = r.json()["reply"]
    assert "Рино" in reply and "Reno" not in reply, reply
    assert r.json()["place"]["name"] == "Reno, NV"  # the card keeps the geocoder's name: the app routes by it


# ---- S17, bug 9: templates instead of the model, and the model's voice ----------------------------------------

S17 = {
    "You're on I-80 in West Sacramento, California.": "Ты на I-80, это West Sacramento, California.",
    "You're in Lodi, California.": "Ты в Lodi, California.",
    "I can't tell where we are right now.": "Сейчас не могу понять, где мы.",
    "There's a way 1 h 45 min slower that saves about $31. Want it?":
        "Есть путь на 1 час 45 минут дольше, экономия около $31. Поедем по нему?",
    "There's a way about as fast that saves about $12. Want it?":
        "Есть путь примерно за то же время, экономия около $12. Поедем по нему?",
    "This is already the cheapest way.": "Это и так самый дешёвый путь.",
    "This is already the fastest way.": "Это и так самый быстрый путь.",
    "I can't price the routes right now. Try again in a minute.":
        "Сейчас не могу посчитать, сколько стоят маршруты. Попробуй через минуту.",
    "Here's another way via I-80 and CA-89, 4 h 33 min, 204 miles, about 1 h 42 min longer, about $5 cheaper in fuel and tolls.":
        "Вот другой путь через I-80 и CA-89, 4 часа 33 минуты, 204 мили, примерно на 1 час 42 минуты дольше, "
        "примерно на $5 дешевле по топливу и платным дорогам.",
    "I don't have a diesel price for Love's Travel Stop Lodi.": "Цены на дизель в Love's Travel Stop Lodi у меня нет.",
}


@pytest.mark.parametrize("en", list(S17))
def test_s17_answers_have_templates(en):
    got, misses = _ru(en)
    assert not misses and got == S17[en]


def test_s17_answers_cost_no_model_call(fake_llm):
    for en, ru in S17.items():
        assert run(i18n.localize(en, "ru")) == ru
    assert fake_llm.calls == []


def test_fallback_prompt_sets_the_voice_and_the_words(fake_llm):
    fake_llm.default = "Заезжай с юго-западной стороны."
    run(i18n.localize("Use the southwest driveway.", "ru"))
    system = fake_llm.calls[0][0]
    assert "«ты»" in system and "«трак»" in system and "«трейлер»" in system and "полуприцеп" in system
    assert all(k[1] == i18n.FALLBACK_VERSION for k in i18n._model_cache)  # an older prompt's lines are not reused


# ---- ru.json fixes (S21–S26, S31, S32) ------------------------------------------------------------------------

@pytest.mark.parametrize("en,ru", [
    ("Added Pilot. New total 3 h 10 min, 150 miles, counting 21 minutes at the stop.",
     "Добавил Pilot. Всего теперь 3 часа 10 минут, 150 миль, с учётом 21 минуты на остановку."),
    ("You already have eight favorites. Remove one first.", "У тебя уже восемь любимых трак-стопов. Сначала убери один."),
    ("Your truck is over the legal weight limit in CA and NV (80,000 lb); that takes a permit.",
     "Твой трак больше законного лимита по весу в CA и NV (80 000 фунтов), нужен пермит."),
    ("No demo places here. Demo data covers the Bay Area and Sacramento.",
     "Здесь демо-мест нет. Демо-данные есть только по району Сан-Франциско и Сакраменто."),
    ("No chain controls on your route right now.", "Сейчас цепи на маршруте нигде не нужны."),
    ("And 3 more chain controls along the route.", "И ещё в 3 местах по маршруту нужны цепи."),
    ("I'll remind you to take a break every 1 hour.", "Буду напоминать о перерыве каждый 1 час."),
    ("The weight has to be between 5,000 and 200,000 pounds.", "Вес должен быть от 5 000 до 200 000 фунтов."),
    ("Weight is now 81,000 pounds.", "Вес теперь 81 000 фунтов."),  # not "81 фунт": the comma is a thousands one
])
def test_catalog_fixes(en, ru):
    got, misses = _ru(en)
    assert not misses and got == ru
    assert not re.search(r"\bфур|грузовик|полуприцеп|контрол", got)


def test_catalog_says_trak_everywhere():
    _tpls, words = i18n.catalog("ru")
    for t in _tpls:
        assert not re.search(r"\bфур[аыуе]?\b|грузовик|полуприцеп|маршрутизатор", t.target), t.target


# ---- S10, S18, S19: the route on the way out ------------------------------------------------------------------

def _route(**kw) -> RouteInfo:
    return RouteInfo(geometry=[[38.58, -121.49], [39.53, -119.81]], distance_m=210000, duration_s=9000, distance_mi=131,
                     duration_min=150, summary="2 h 30 min · 131 mi", source="here", **kw)


def _localized(route: RouteInfo) -> RouteInfo:
    async def go():
        i18n.set_lang("ru")
        return i18n.localize_route(route)
    return run(go())


def test_incidents_weather_and_chains_are_translated_on_a_copy():
    route = _route(
        incidents=[Incident(type="construction", closed=False, description="Road construction. Two lanes closed", index=1),
                   Incident(type="accident", closed=True, description="Accident", index=1),
                   Incident(type="other", closed=False, description="Truck rollover blocking the tunnel exit", index=1)],
        weather=[WeatherAlert(id="a", event="Winter Storm Warning", severity="Severe", headline="", area="Tahoe", index=1),
                 WeatherAlert(id="b", event="High Wind Warning", severity="Severe", headline="", area="Tahoe", index=1)],
        chain_controls=[ChainControl(id="c", route="I-80", location="Kingvale", lat=39.3, lng=-120.4, status="R-2",
                                     description="Chains are required on all vehicles.")])
    out = _localized(route)
    assert [(i.type, i.description) for i in out.incidents] == [
        ("Ремонт дороги", "Ремонт дороги. Две полосы закрыты"), ("Авария", "Авария"),
        ("Происшествие", "Truck rollover blocking the tunnel exit")]  # unknown text: English whole, not a mix
    assert [a.event for a in out.weather] == ["Предупреждение о зимнем шторме", "Штормовое предупреждение о ветре"]
    assert out.chain_controls[0].description == "Цепи нужны всем машинам."
    assert route.incidents[0].type == "construction" and route.weather[0].event == "Winter Storm Warning"


def test_backup_route_notice_is_translated():
    out = _localized(_route(notices=[routing.NO_TURNS_NOTICE, "Tolls couldn't be fully avoided on this route."]))
    assert out.notices == [
        "Сервис маршрутов для траков сейчас не работает, это запасной маршрут: подсказок поворотов не будет, "
        "езжай по линии на карте.",
        "Все платные дороги на этом маршруте объехать не получилось."]


@pytest.mark.parametrize("en,ru", [
    ("Take middle ramp onto I-5 N toward Redding", "Заезжай на средний съезд на I-5 N в сторону Redding"),
    ("Take left ramp onto I-80-BR W (Capital City Fwy) toward US-50 W",
     "Заезжай на левый съезд на I-80-BR W (Capital City Fwy) в сторону US-50 W"),
    ("Take right ramp toward Reno", "Заезжай на правый съезд в сторону Reno"),
    ("Keep middle", "Держись посередине"),
    ("Keep middle onto I-5 N toward Redding", "Держись посередине на I-5 N в сторону Redding"),
    ("Take the left exit 23 toward Reno", "Уходи на левый съезд 23 в сторону Reno"),
    ("Take exit 522", "Съезд 522"),
    ("Head toward J St on 9th St", "Езжай по 9th St в сторону J St"),
    ("Turn sharply left onto Main St", "Поверни резко налево на Main St"),
    ("Merge onto I-80 W toward Sacramento", "Перестраивайся на I-80 W в сторону Sacramento"),
    ("Take the 7th exit from roundabout onto Elm Ave", "На круге седьмой съезд на Elm Ave"),
])
def test_maneuvers(en, ru):
    out = _localized(_route(maneuvers=[Maneuver(action="turn", instruction=en, lat=0, lng=0, index=0)]))
    assert out.maneuvers[0].instruction == ru


def test_recorded_here_maneuvers_all_translate():
    info = routing._here_parse(fixture_json("here/route_sac_reno"))
    for r in [info, *info.alternatives]:
        for m in r.maneuvers:
            got, misses = _ru(m.instruction)
            assert not misses, m.instruction


def test_unknown_maneuver_stays_english_whole():
    en = "Arrive at Elm Ave. Drive through the gate and follow the yard signs"
    out = _localized(_route(maneuvers=[Maneuver(action="arrive", instruction=en, lat=0, lng=0, index=0)]))
    assert out.maneuvers[0].instruction == en  # not "Прибытие: Elm Ave. Drive through ..."


# ---- S7: the breakdown card and lines -------------------------------------------------------------------------

def test_call_card_is_translated_and_share_text_stays_english():
    truck = TruckProfile(height_in=162, trailer_ft=53, weight_lb=80000, hazmat=True)
    card = CallCard(problem="пробил колесо", tire="drive tire 295/75R22.5", gps="38.58160, -121.49440",
                    map_link="https://maps.google.com/?q=38.58160,-121.49440", truck=truck.label,
                    questions=["What's your road call fee?", "What's your ETA to me?", "Do you take Comchek or EFS?"],
                    share_text="I'm stuck at Elm Ave. GPS 38.58160, -121.49440.")

    async def go():
        i18n.set_lang("ru")
        return i18n.localize_call_card(card)
    out = run(go())
    assert out.questions == ["Сколько стоит выезд?", "Когда сможете ко мне приехать?", "Принимаете Comchek или EFS?"]
    assert out.tire == "Ведущее колесо, 295/75R22.5"
    assert out.truck == ("Высота 13 футов 6 дюймов, трейлер 53 фута (всего около 73 футов), полная масса "
                         "80 000 фунтов, опасный груз")
    assert out.share_text == card.share_text and out.problem == card.problem
    assert i18n.localize_call_card(card) is card  # English: untouched


@pytest.mark.parametrize("en", [
    "I couldn't find anyone nearby who comes out to you. The closest shop is Bob's, about 4 miles, but you'd need a tow "
    "to get there. Tap it to call, or ask me to look for a tow.",
    "Nobody nearby says they come out. Closest shop: Bob's, 4.1 mi, needs a tow.",
    "I couldn't pull reviews, here's who's nearby. The closest is Bob's, about 4 miles.",
    "Okay, here's what I found. Best bet is Joe's Tire, about 3 miles and they come out to you, 4.7 stars. Tap one to "
    "call, or ask me anything about them.",
])
def test_breakdown_lines_have_templates(en):
    got, misses = _ru(en)
    assert not misses and not re.search(r"\b(miles|about|nearby|shop)\b", got), got


# ---- fractions: a decimal comma and the genitive singular ("около 3,2 мили") -----------------------------------

@pytest.mark.parametrize("n,forms,want", [
    (3.2, ("миля", "мили", "миль"), "мили"), (0.5, ("мили", "миль", "миль"), "мили"),
    (1.5, ("миле", "милях", "милях", "мили"), "мили"), (2.5, ("фут", "фута", "футов"), "фута"),
    (21, ("миле", "милях", "милях", "мили"), "миле"), (5, ("мили", "миль", "миль"), "миль"),
])
def test_plural_of_a_fraction_is_genitive_singular(n, forms, want):
    assert i18n.plural_ru(n, *forms) == want


@pytest.mark.parametrize("en,ru", [
    ("Pilot Travel Center, right off Elm Ave, about 3.2 miles out.", "Pilot Travel Center, рядом с Elm Ave, примерно в 3,2 мили."),
    ("Pilot Travel Center, 0.5 miles away.", "Pilot Travel Center, в 0,5 мили."),
    ("Pilot Travel Center is about 1.5 miles ahead.", "Pilot Travel Center примерно через 1,5 мили."),
    ("Pilot Travel Center, 21 miles away.", "Pilot Travel Center, в 21 миле."),
    ("I couldn't pull reviews, here's who's nearby. The closest is Bob's, about 4.1 miles.",
     "Отзывы получить не удалось, вот кто есть рядом. Ближайший: Bob's, около 4,1 мили."),
    ("Diesel's $3.45 there.", "Дизель там по $3,45."),
    ("drive tire 295/75R22.5", "Ведущее колесо, 295/75R22.5"),  # a tire size is a code, not a number
])
def test_fractions_in_russian(en, ru):
    got, misses = _ru(en)
    assert not misses and got == ru


def test_english_fractions_untouched():
    assert i18n.translate("Pilot Travel Center, 0.5 miles away.", "en") == "Pilot Travel Center, 0.5 miles away."
