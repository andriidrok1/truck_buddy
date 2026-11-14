"""The second Russian QA pass, 2026-11-11 (vault: «TruckBuddy — QA русской версии, второй проход: ответы копилота»),
server side: a weather watch reads as a noun in every weather line, the accusative after «через», the break reminder
for one hour, arrival at "your destination" on a side, HERE incident descriptions, Latin words a model translation
left behind, and "где встать на ночь" as a truck stop."""
import re

import pytest
from conftest import run

from models import AgentRequest, Incident, Maneuver, Place, RouteInfo, TruckProfile
from services import i18n

SAC = {"lat": 38.5816, "lng": -121.4944}


@pytest.fixture(autouse=True)
def _real_catalog():
    i18n.catalog.cache_clear()  # test_i18n swaps in a small catalog of its own
    yield
    i18n.catalog.cache_clear()


def _ru(text: str) -> tuple[str, list[str]]:
    misses: list[str] = []
    return i18n.translate(text, "ru", fallback=lambda s, c: misses.append(s) or None), misses


def test_placeholders_of_every_template_exist_in_its_english():
    """The catalog's own consistency: a Russian template can only use a value its English side captures."""
    tpls, _words = i18n.catalog("ru")
    for t in tpls:
        en = {m.group(1) for m in i18n._PH.finditer(t.en)}
        ru = {m.group(1) for m in i18n._PH.finditer(t.target)}
        assert ru <= en, (t.en, t.target)


# ---- weather watches (item 2) ---------------------------------------------------------------------------------

WATCHES = {
    "High Wind Watch": "угроза сильного ветра", "Winter Storm Watch": "угроза зимнего шторма",
    "Hurricane Watch": "угроза урагана", "Tropical Storm Watch": "угроза тропического шторма",
    "Freeze Watch": "угроза заморозков", "Fire Weather Watch": "угроза пожароопасной погоды",
    "Extreme Cold Watch": "угроза сильного мороза", "Flood Watch": "угроза наводнения",
    "Flash Flood Watch": "угроза внезапного наводнения", "Severe Thunderstorm Watch": "угроза сильной грозы",
    "Tornado Watch": "угроза торнадо", "Avalanche Watch": "угроза лавин",
}
OTHER_EVENTS = {
    "Winter Storm Warning": "предупреждение о зимнем шторме", "High Wind Warning": "штормовое предупреждение о ветре",
    "Wind Advisory": "предупреждение о ветре", "Red Flag Warning": "пожароопасная погода",
    "Dense Fog Advisory": "предупреждение о густом тумане",
}
# Every line the code builds around an event (services/weather.py, agent replies), with the Russian it must give.
EVENT_LINES = [
    ("Heads up, a {e} near Lassen Volcanic National Park starts Friday 10 am; at this pace you're through there by 11:30 pm.",
     "Внимание, {r} в районе Lassen Volcanic National Park начнётся в пятницу в 10:00; в таком темпе проедешь там к 23:30."),
    ("Heads up, there's a {e} in effect there.", "Внимание, там действует {r}."),
    ("Weather warning: {e} ahead near Donner Pass.", "Погодное предупреждение: впереди {r} в районе Donner Pass."),
    ("Weather warning: {e} near Donner Pass, you'd hit it around 3:30 pm.",
     "Погодное предупреждение: {r} в районе Donner Pass, доедешь туда примерно в 15:30."),
    ("Weather warning: {e} near Donner Pass, you'd hit it around 3:30 pm, in effect till 8:58 pm.",
     "Погодное предупреждение: {r} в районе Donner Pass, доедешь туда примерно в 15:30, действует до 20:58."),
    ("{e} near Donner Pass.", "{R} в районе Donner Pass."),
    ("{e} and 1 more weather alert along the route.", "{R} и ещё 1 погодное предупреждение по маршруту."),
    ("{e} and 2 more weather alerts along the route.", "{R} и ещё 2 предупреждения по маршруту."),
    ("{e} near Donner Pass around 3:30 pm and 1 more weather alert along the route.",
     "{R} в районе Donner Pass около 15:30 и ещё 1 погодное предупреждение по маршруту."),
    ("{e} near Donner Pass around 3:30 pm and 2 more weather alerts along the route.",
     "{R} в районе Donner Pass около 15:30 и ещё 2 предупреждения по маршруту."),
    ("The way via I-80 only runs into a {e}. Say 'take the other way' if you want it.",
     "На пути через I-80 только {r}. Скажи «давай другим путём», если хочешь."),
    ("The way via I-80 only runs into a {e}, about 12 min longer. Say 'take the other way' if you want it.",
     "На пути через I-80 только {r}, примерно на 12 минут дольше. Скажи «давай другим путём», если хочешь."),
    ("The way via I-80 only runs into a {e} and takes about the same time. Say 'take the other way' if you want it.",
     "На пути через I-80 только {r} и по времени примерно так же. Скажи «давай другим путём», если хочешь."),
    ("The way via I-80 misses it and takes about the same time.", "Путь через I-80 это обходит и по времени примерно так же."),
    ("The way via I-80 only runs into a {e}, about 12 min longer.", "На пути через I-80 только {r}, примерно на 12 минут дольше."),
    # no road name for the other way (weather.detour_speech): a sentence of its own, not "На пути через the other road"
    ("The way via the other road only runs into a {e}, about 21 min longer. Say 'take the other way' if you want it.",
     "На другой дороге только {r}, примерно на 21 минуту дольше. Скажи «давай другим путём», если хочешь."),
    ("The way via the other road only runs into a {e}, about 12 min longer.", "На другой дороге только {r}, примерно на 12 минут дольше."),
    ("Here's another way via I-80 and CA-89, 4 h 29 min, 204 miles, about 1 h 40 min longer, and it misses the {e}.",
     "Вот другой путь через I-80 и CA-89, 4 часа 29 минут, 204 мили, примерно на 1 час 40 минут дольше, и он обходит участок, где {r}."),
    ("Here's another way via I-80, 4 h 29 min, 204 miles, about 1 h 40 min longer, but it runs into a {e}.",
     "Вот другой путь через I-80, 4 часа 29 минут, 204 мили, примерно на 1 час 40 минут дольше, но там {r}."),
]


def test_every_event_template_is_exercised():
    """The list comes from the catalog: a new template with {event} fails here until EVENT_LINES has a line for it,
    so a watch is checked in it too."""
    tpls, _words = i18n.catalog("ru")
    with_event = [t for t in tpls if "{event" in t.target]
    assert len(with_event) >= 16
    lines = [en.replace("{e}", "Wind Advisory") for en, _ru_line in EVENT_LINES]
    for t in with_event:
        assert "{event|nom}" in t.target, t.target  # the only form the words table holds for an event
        assert any(t.rx.search(line) for line in lines), f"no line in EVENT_LINES for: {t.en}"


def test_detour_line_as_the_code_builds_it():
    """weather.DETOUR_LIGHTER with no road name and every shape of the time difference."""
    from services import weather
    for diff, tail in (("", ""), (", about 1 h 21 min longer", ", примерно на 1 час 21 минуту дольше"),
                       (", about 12 min faster", ", примерно на 12 минут быстрее"),
                       (weather.SAME_TIME, " и по времени примерно так же")):
        got, misses = _ru(weather.DETOUR_LIGHTER.format(via="the other road", event="Wind Advisory", diff=diff))
        assert not misses
        assert got == f"На другой дороге только предупреждение о ветре{tail}. Скажи «давай другим путём», если хочешь."


@pytest.mark.parametrize("event,noun", list(WATCHES.items()) + list(OTHER_EVENTS.items()))
def test_an_event_is_a_noun_in_every_weather_line(event, noun):
    for en, ru in EVENT_LINES:
        got, misses = _ru(en.replace("{e}", event))
        assert not misses, misses
        assert got == ru.replace("{r}", noun).replace("{R}", noun[:1].upper() + noun[1:]), en
        assert "возмож" not in got  # the clause that made two predicates: "возможен зимний шторм ... начнётся"


def test_every_watch_in_the_words_table_is_a_noun():
    _tpls, words = i18n.catalog("ru")
    watches = {k: v for k, v in words.items() if k.endswith(" watch")}
    assert set(watches) == {k.lower() for k in WATCHES}
    assert all(isinstance(v, str) and v.startswith("угроза ") for v in watches.values()), watches


def test_the_route_line_from_the_report():
    got, misses = _ru("Okay. Route to Reno is set, 2 h 49 min, about 132 miles. Heads up, a Winter Storm Watch near Lassen "
                      "Volcanic National Park starts Friday 10 am; at this pace you're through there by 11:30 pm.")
    assert not misses
    assert got == ("Хорошо. Маршрут до Reno готов, 2 часа 49 минут, около 132 миль. Внимание, угроза зимнего шторма в "
                   "районе Lassen Volcanic National Park начнётся в пятницу в 10:00; в таком темпе проедешь там к 23:30.")


# ---- the accusative after «через» (item 11) -------------------------------------------------------------------

@pytest.mark.parametrize("mi,want", [("1", "1 милю"), ("2", "2 мили"), ("5", "5 миль"), ("11", "11 миль"), ("21", "21 милю"),
                                     ("22", "22 мили"), ("101", "101 милю"), ("0.5", "0,5 мили"), ("1.5", "1,5 мили")])
def test_miles_after_cherez(mi, want):
    lines = {
        f"Your break is due in 21 minutes. There's a Pilot in {mi} miles, want me to add it?":
            f"Перерыв через 21 минуту. Через {want} есть Pilot, добавить?",
        f"Your break is due now. There's a Pilot in {mi} miles, want me to add it?":
            f"Пора на перерыв. Через {want} есть Pilot, добавить?",
        f"There's a Pilot in {mi} miles, want me to add it?": f"Через {want} есть Pilot, добавить?",
        f"It's getting late. The Pilot in {mi} miles, should I route you there?": f"Уже поздно. Pilot через {want}, проложить туда?",
        f"About 40 minutes on the shift. The Pilot in {mi} miles, should I route you there?":
            f"На смене осталось около 40 минут. Pilot через {want}, проложить туда?",
        f"The Pilot in {mi} miles, should I route you there?": f"Pilot через {want}, проложить туда?",
        f"Pilot Travel Center is about {mi} miles ahead.": f"Pilot Travel Center примерно через {want}.",
        f"Pilot Travel Center is about {mi} miles ahead, just up Elm Ave.": f"Pilot Travel Center примерно через {want}, дальше по Elm Ave.",
        f"Nearest CAT scale is at Pilot, about {mi} miles ahead.": f"Ближайший кэт-скейл на Pilot, примерно через {want}.",
        f"The last truck stop before the control is Pilot, {mi} miles ahead, want me to add it?":
            f"Последний трак-стоп до участка с цепями: Pilot, через {want}, добавить?",
        f"The last truck stop before it is Pilot, {mi} miles ahead, want me to add it?":
            f"Последний трак-стоп перед этим: Pilot, через {want}, добавить?",
    }
    for en, ru in lines.items():
        got, misses = _ru(en)
        assert not misses and got == ru, en


def test_no_template_puts_a_nominative_after_cherez():
    tpls, _words = i18n.catalog("ru")
    bad = [t.target for t in tpls if re.search(r"через \{\w+\|plural:(?:миля|минута|фут,|час,)", t.target, re.I)]
    assert not bad, bad
    assert sum(1 for t in tpls if re.search(r"через \{mi\|plural:милю,мили,миль\}", t.target, re.I)) == 15


# ---- the break reminder for one hour (item 12) ----------------------------------------------------------------

@pytest.mark.parametrize("en,ru", [
    ("I'll remind you to take a break every 1 hour.", "Буду напоминать о перерыве каждый час."),
    ("I'll remind you to take a break every 2 hours.", "Буду напоминать о перерыве каждые 2 часа."),
    ("I'll remind you to take a break every 5 hours.", "Буду напоминать о перерыве каждые 5 часов."),
    ("Done, height is now 13 feet, I'll remind you to take a break every 1 hour.",
     "Готово: высота теперь 13 футов, буду напоминать о перерыве каждый час."),
    ("Done, height is now 13 feet, I'll remind you to take a break every 3 hours.",
     "Готово: высота теперь 13 футов, буду напоминать о перерыве каждые 3 часа."),
])
def test_break_reminder_hours(en, ru):
    got, misses = _ru(en)
    assert not misses and got == ru


# ---- every control answer through the real controls.apply_actions (review of #168) -----------------------------

PILOT = Place(id="p", name="Pilot Travel Center", category="truck_stop", lat=38.6, lng=-121.5)
CONTROLS = [
    ("set_height_in", 162, "высота теперь 13 футов 6 дюймов"), ("set_height_in", 156, "высота теперь 13 футов"),
    ("set_trailer_ft", 40, "трейлер теперь 40 футов"), ("set_weight_lb", 70000, "вес теперь 70\u00a0000 фунтов"),
    ("set_hazmat", True, "hazmat включён, поведу по дорогам, где он разрешён"), ("set_hazmat", False, "hazmat выключен"),
    ("avoid_tolls", True, "буду объезжать платные дороги"), ("avoid_tolls", False, "платные дороги теперь можно"),
    ("avoid_ferries_tunnels", True, "буду объезжать паромы и тоннели"),
    ("avoid_ferries_tunnels", False, "паромы и тоннели теперь можно"),
    ("add_favorite_chain", "Pilot", "добавил Pilot в любимые"), ("remove_favorite_chain", "TA", "убрал TA из любимых"),
    ("set_break_hours", 1, "буду напоминать о перерыве каждый час"),
    ("set_break_hours", 3, "буду напоминать о перерыве каждые 3 часа"),
    ("set_break_hours", 0, "напоминания о перерыве выключены"),
]
REROUTES = {"set_height_in", "set_trailer_ft", "set_weight_lb", "set_hazmat", "avoid_tolls", "avoid_ferries_tunnels"}
NOT_DONE = [  # answers that do not start with "Done, "
    ("set_driver_name", "Mike", "Понял, буду звать тебя Mike."), ("set_chains_on_board", True, "Понял, цепи есть."),
    ("set_chains_on_board", False, "Понял, цепей нет."), ("set_nudges", True, "Снова буду подсказывать."),
    ("set_nudges", False, "Хорошо, больше не буду ничего предлагать."),
    ("voice_guidance", True, "Хорошо, буду подсказывать повороты."), ("voice_guidance", False, "Хорошо, подсказки поворотов выключены."),
    ("recenter", None, "Снова на тебе."), ("zoom_in", None, "Приближаю."), ("zoom_out", None, "Отдаляю."),
]


def _control(actions: list, active: bool = False) -> str:
    from services.controls import apply_actions
    req = AgentRequest(text="", active_place=PILOT if active else None, truck_profile=TruckProfile(favorite_chains=["TA"]), **SAC)
    _acts, say, _end = apply_actions([{"type": k, "value": v} for k, v in actions], req)
    got, misses = _ru(say)
    assert not misses, (say, misses)
    return got


def _no_english(text: str) -> bool:
    return not i18n.latin_left(text) and not re.search(r"\b(?:is|are|now|it|on|off|I'll|to|the|for)\b", text)


@pytest.mark.parametrize("kind,value,ru", CONTROLS)
def test_a_single_control_answer_is_whole_russian(kind, value, ru):
    assert _control([(kind, value)]) == f"Готово: {ru}."
    tail = ", перестраиваю маршрут" if kind in REROUTES else ""
    assert _control([(kind, value)], active=True) == f"Готово: {ru}{tail}."


@pytest.mark.parametrize("kind,value,ru", NOT_DONE)
def test_control_answers_without_done(kind, value, ru):
    got = _control([(kind, value)])
    assert got == ru and _no_english(got.replace("Mike", ""))


def test_every_control_action_type_is_covered():
    """A new action type in controls.py must get a line above."""
    import inspect
    from services import controls
    kinds = set(re.findall(r'kind == "(\w+)"', inspect.getsource(controls.apply_actions)))
    kinds |= {"recenter", "zoom_in", "zoom_out"}
    seen = {k for k, _v, _r in CONTROLS + NOT_DONE}
    # these need a route or the options list on screen; their lines are in the catalog since the first Russian pass
    assert kinds - seen == {"start_navigation", "end_route", "show_options", "overview", "wake_word", "stop_talking"}


def test_combined_control_answers():
    """Every data control in one answer, each in every position: first (bare) and after a comma."""
    data = [c for c in CONTROLS if c[0] != "remove_favorite_chain"]
    for shift in range(len(data)):
        order = data[shift:] + data[:shift]
        picked: dict = {}
        for kind, value, ru in order:  # one value per action type, as one driver sentence would give
            picked.setdefault(kind, (value, ru))
        got = _control([(k, v) for k, (v, _r) in picked.items()], active=True)
        assert got == "Готово: " + ", ".join(r for _v, r in picked.values()) + ", перестраиваю маршрут."
        assert _no_english(got.replace("Pilot", ""))
    got = _control([("set_trailer_ft", 40), ("set_driver_name", "Mike"), ("set_chains_on_board", True)])
    assert got == "Готово: трейлер теперь 40 футов, понял, буду звать тебя Mike, понял, цепи есть."


# ---- arrival at "your destination" on a side (item 5) ---------------------------------------------------------

@pytest.mark.parametrize("en,ru", [
    ("Arrive at your destination on the left", "Ты на месте, цель слева"),
    ("Arrive at your destination on the right", "Ты на месте, цель справа"),
    ("Arrive at your destination, on the left", "Ты на месте, цель слева"),  # the comma HERE sends now and then
    ("Arrive at your destination, on the right", "Ты на месте, цель справа"),
    ("Arrive at your destination on the left.", "Ты на месте, цель слева."),
    ("Arrive at your destination", "Ты на месте"),
    ("Arrive at your destination.", "Ты на месте."),
    # a named place still goes through the generic template, with one comma either way
    ("Arrive at Pilot Travel Center on the left", "Прибытие: Pilot Travel Center, слева"),
    ("Arrive at Pilot Travel Center, on the right", "Прибытие: Pilot Travel Center, справа"),
    ("Arrive at Pilot Travel Center on the right.", "Прибытие: Pilot Travel Center, справа."),
    ("Arrive at 16th St", "Прибытие: 16th St"),
])
def test_arrival(en, ru):
    got, misses = _ru(en)
    assert not misses and got == ru
    assert "your destination" not in got and ",," not in got


def test_exact_arrival_template_is_tried_before_the_generic_one():
    tpls, _words = i18n.catalog("ru")
    order = [t.en for t in tpls]
    for side in ("left", "right"):
        assert order.index(f"Arrive at your destination on the {side}") < order.index(f"Arrive at {{place}} on the {side}")
        assert order.index(f"Arrive at your destination, on the {side}") < order.index(f"Arrive at {{place}}, on the {side}")
        assert order.index(f"Arrive at {{place}}, on the {side}") < order.index(f"Arrive at {{place}} on the {side}")


def _localized(route: RouteInfo) -> RouteInfo:
    async def go():
        i18n.set_lang("ru")
        return i18n.localize_route(route)
    return run(go())


def _route(**kw) -> RouteInfo:
    return RouteInfo(geometry=[[38.58, -121.49], [39.53, -119.81]], distance_m=210000, duration_s=9000, distance_mi=131,
                     duration_min=150, summary="2 h 30 min · 131 mi", source="here", **kw)


def test_last_maneuver_of_a_route_is_whole_russian():
    out = _localized(_route(maneuvers=[
        Maneuver(action="turn", direction="left", instruction="Turn left onto 16th St", lat=38.5, lng=-121.5, index=0),
        Maneuver(action="arrive", instruction="Arrive at your destination on the left", lat=38.6, lng=-121.5, index=1)]))
    assert [m.instruction for m in out.maneuvers] == ["Поверни налево на 16th St", "Ты на месте, цель слева"]


# ---- HERE incident descriptions (item 6) ----------------------------------------------------------------------

def _descriptions(*texts: str) -> list[str]:
    out = _localized(_route(incidents=[Incident(type="other", closed=False, description=t, index=i) for i, t in enumerate(texts)]))
    return [i.description for i in out.incidents]


def test_incident_descriptions_from_the_qa_routes_are_translated():
    """The two descriptions the QA run logged as `i18n miss ru`, as routing._incident stores them (no last period)."""
    assert _descriptions("Major event", "Road construction. Roadway reduced to two lanes", "Accident. Right lane blocked",
                         "Roadway reduced to one lane", "Roadway reduced to 2 lanes", "Three lanes blocked",
                         "Road construction. 2 lanes closed") == [
        "Крупное мероприятие", "Ремонт дороги. Дорога сужена до двух полос", "Авария. Правая полоса перекрыта",
        "Дорога сужена до одной полосы", "Дорога сужена до 2 полос", "Три полосы перекрыты", "Ремонт дороги. Закрыто полос: 2"]


def test_unknown_incident_description_stays_english_whole():
    # one sentence the catalog lacks keeps the whole description English: the app does not say such a description
    texts = ["Road construction. Pilot car operations in the canyon", "Roadway reduced to two narrow lanes"]
    assert _descriptions(*texts) == texts


def test_recorded_here_incident_descriptions_all_translate():
    """Every incident description in the recorded HERE responses has a template."""
    import json
    from pathlib import Path
    seen = set()
    for path in (Path(__file__).resolve().parents[1] / "fixtures" / "here").glob("*.json"):
        seen.update(re.findall(r'"description":\s*"([^"]+)"', json.dumps(json.loads(path.read_text()), ensure_ascii=False)))
    assert {"Road construction", "Major road construction"} <= seen
    for text in seen:
        got = _descriptions(text.rstrip("."))[0]
        assert not re.search(r"[A-Za-z]{4,}", got), (text, got)


def test_incident_wordings_match_only_in_the_incident_field():
    """"Construction", "Closed", "Event" are whole short lines: outside an incident they must not take a place name."""
    for town in ("Construction", "Event", "Closed", "Accident", "Incident", "Weather", "Other"):
        got, _misses = _ru(f"Route to {town} is set, 2 h 52 min, about 132 miles.")
        assert got == f"Маршрут до {town} готов, 2 часа 52 минуты, около 132 миль."
        assert _ru(town)[0] == town
    for text in ("Construction", "Event", "Closed", "Accident", "Incident", "roadClosure"):  # no description: HERE's type
        assert not re.search(r"[A-Za-z]", _descriptions(text)[0])


# ---- English words a model translation left behind (item 9) ---------------------------------------------------

TIP = "Use the southwest driveway off West Capitol Avenue, beside fuel canopy"


@pytest.mark.parametrize("ru,left", [
    ("Используй юго-западный подъезд с West Capitol Avenue, рядом с fuel canopy", ["fuel", "canopy"]),
    ("Въезд для truck на Elm St.", ["truck"]),
    ("Паркуйся за park.", ["park"]),
    ("Через 500 feet налево.", ["feet"]),
    ("Используй юго-западный подъезд с West Capitol Avenue, рядом с навесом у заправки", []),
    ("В заднем ряду нельзя парковать hazmat.", []),            # the catalog itself keeps hazmat Latin
    ("Паркуйся у входа в Walmart.", []),                       # the source had "walmart" in lower case
    ("Паркуйся у входа в walmart.", []),
    ("Используй въезд с El Centro Road.", []),                 # source "el centro road"
    ("Въезд с West Capitol Avenue.", []),                      # source "W Capitol Ave": the model expanded it
    ("Заезжай у CAT scale.", []),
    ("Паркуйся за Loves.", []), ("Паркуйся за Pilot's.", []),  # a source name spelled another way
    ("Паркуйся за Love’s, там есть wifi.", []),
    ("Посмотри место на truckparkingclub.com.", []), ("Смотри https://example.org/lots/west?x=1 перед выездом.", []),
    ("На въезде знак «trucks only».", []), ('На въезде знак "trucks only".', []),
    ("Езжай по I-80 E в сторону Reno, 13 ft 6 in под мостом, не быстрее 25 mph.", []),
])
def test_latin_left(ru, left):
    assert i18n.latin_left(ru) == left


def _model(monkeypatch, answers: dict):
    """services.llm.complete stubbed: the translation by source sentence, and the calls made."""
    from services import llm
    calls: list[str] = []

    async def complete(system, user):
        calls.append(user)
        return answers[user]

    monkeypatch.setattr(llm, "complete", complete)
    monkeypatch.setattr(i18n, "_model_cache", type(i18n._model_cache)())
    return calls


def _localize_ru(text: str) -> str:
    async def go():
        i18n.set_lang("ru")
        return await i18n.localize(text)
    return run(go())


def test_model_translation_with_english_left_stays_english_whole(monkeypatch):
    calls = _model(monkeypatch, {TIP: "Используй юго-западный подъезд с West Capitol Avenue, рядом с fuel canopy"})
    assert _localize_ru(TIP) == TIP  # as for a translation that changed a number: the English, not a mix
    assert calls == [TIP]


def test_a_rejected_translation_is_paid_for_once(monkeypatch):
    """The tip is the same on every request for that place: five requests, one model call."""
    calls = _model(monkeypatch, {TIP: "Используй юго-западный подъезд с West Capitol Avenue, рядом с fuel canopy",
                                 "Stay 3 feet off the fence": "Держись в 5 футах от забора"})
    for _ in range(5):
        assert _localize_ru(TIP) == TIP
        assert _localize_ru(f"Alright. {TIP}.") == f"Хорошо. {TIP}."
        assert _localize_ru("Stay 3 feet off the fence.") == "Stay 3 feet off the fence."  # a changed number, the same way
    assert calls == [TIP, "Stay 3 feet off the fence"]


def test_fallback_prompt_forbids_english_words_and_has_a_new_cache_version():
    assert "no English word may be left" in i18n.fallback_prompt("ru")
    assert i18n.FALLBACK_VERSION == 3


def test_clean_model_translation_is_used(monkeypatch):
    good = "Используй юго-западный подъезд с West Capitol Avenue, рядом с навесом у заправки"
    calls = _model(monkeypatch, {TIP: good})
    assert _localize_ru(TIP) == good and calls == [TIP]
    assert _localize_ru(TIP) == good and calls == [TIP]  # and cached


def test_only_the_bad_sentence_stays_english(monkeypatch):
    first, second = "Enter from the north gate on Elm Ave.", "Park beside the fuel canopy."
    _model(monkeypatch, {first.rstrip("."): "Заезжай через северные ворота с Elm Ave", second.rstrip("."): "Паркуйся рядом с fuel canopy"})
    assert _localize_ru(f"Alright. {first} {second}") == f"Хорошо. Заезжай через северные ворота с Elm Ave. {second}"


def test_sync_fallback_is_checked_the_same_way():
    out = i18n.translate("Park beside the fuel canopy.", "ru", fallback=lambda s, c: "Паркуйся рядом с fuel canopy.")
    assert out == "Park beside the fuel canopy."
    out = i18n.translate("Park beside the fuel canopy.", "ru", fallback=lambda s, c: "Паркуйся рядом с навесом у заправки.")
    assert out == "Паркуйся рядом с навесом у заправки."


# ---- "где встать на ночь" is a truck stop (item 4) -------------------------------------------------------------

def test_prompt_sends_overnight_parking_to_a_truck_stop():
    from services.agent import PROMPT_CACHE_KEY, PROMPT_VERSION, SYSTEM_STATIC
    truck_stop = re.search(r"truck_stop = (.*?);", SYSTEM_STATIC, re.S).group(1)
    hotel = re.search(r"hotel = (.*?);\n\s+rest_area", SYSTEM_STATIC, re.S).group(1)
    assert "park the truck for the night" in truck_stop and "где встать на ночь" in truck_stop
    assert not re.search(r"hotel = [^(]*overnight", SYSTEM_STATIC)  # "overnight" no longer lists under hotel
    assert "means parking the truck, not a hotel" in hotel and "motel" in hotel
    assert PROMPT_VERSION == 7 and PROMPT_CACHE_KEY == "agent-v7"  # a changed prompt gets a new cache key


PARKING_RU = ["где встать на ночь", "где припарковать трак на ночь", "нужна стоянка на ночь", "где тут переночевать",
              "Где можно постоять ночью", "где ночевать", "где переночую", "где остановиться на ночь",
              "где можно поспать в траке", "хочу спать в кабине", "где запарковаться на ночь", "нужен паркинг на ночь",
              "где встать на ночь, чтобы был душ"]  # showers are a truck-stop thing
BED_RU = ["нужен мотель на ночь", "хочу поспать в номере", "найди отель со стоянкой для трака", "я устал, хочу спать",
          "я устал стоять в пробке, найди где поспать", "хочу нормальную постель, надоело стоять на стоянках",
          "мотель с душем"]


def _filed_as_hotel(agent_sandbox, said: str, lang: str = "ru"):
    agent_sandbox.llm.default = {"intent": "find", "category": "hotel"}
    return run(agent_sandbox.provider().handle(AgentRequest(text=said, session_id=f"g-{said}", lang=lang, **SAC), None))


@pytest.mark.parametrize("said", PARKING_RU)
def test_russian_parking_filed_as_hotel_becomes_a_truck_stop(agent_sandbox, fake_http, said):
    """The guard behind the prompt (tests/offline/test_park_not_hotel.py), for the Russian words."""
    res = _filed_as_hotel(agent_sandbox, said)
    assert res.category == "truck_stop" and res.status in ("route_set", "no_results")


@pytest.mark.parametrize("said", BED_RU)
def test_a_russian_ask_for_a_bed_stays_a_hotel(agent_sandbox, fake_http, said):
    assert _filed_as_hotel(agent_sandbox, said).category == "hotel"


@pytest.mark.parametrize("said,category", [("where can I park tonight, somewhere with a shower", "truck_stop"),
                                           ("a motel with a shower and truck parking", "hotel")])
def test_a_shower_is_not_a_bed_word(agent_sandbox, fake_http, said, category):
    assert _filed_as_hotel(agent_sandbox, said, "en").category == category


# ---- small ones from the review of #168 ------------------------------------------------------------------------

@pytest.mark.parametrize("en,ru", [
    ("Make a U-Turn onto West Capitol Ave", "Развернись на West Capitol Ave"),  # capital T, from the miss log
    ("Make a U-Turn", "Развернись"), ("Make a U-turn onto West Capitol Ave", "Развернись на West Capitol Ave"),
    ("Here's another way via I-80, 4 h 29 min, 204 miles, about 1 h 21 min longer.",
     "Вот другой путь через I-80, 4 часа 29 минут, 204 мили, примерно на 1 час 21 минуту дольше."),
    ("Here's another way via I-80, 2 h 49 min, 132 miles, about 1 min faster.",
     "Вот другой путь через I-80, 2 часа 49 минут, 132 мили, примерно на 1 минуту быстрее."),
    ("Here's another way via I-80, 2 h 49 min, 132 miles, about 1 h 59 min faster.",
     "Вот другой путь через I-80, 2 часа 49 минут, 132 мили, примерно на 1 час 59 минут быстрее."),
])
def test_u_turn_and_minutes_after_na(en, ru):
    got, misses = _ru(en)
    assert not misses and got == ru


def test_time_forms():
    assert i18n.time_ru("1 h 21 min", "acc") == "1 час 21 минуту" and i18n.time_ru("21 min") == "21 минута"
    assert i18n.time_ru("2 h 2 min", "acc") == "2 часа 2 минуты" and i18n.time_ru("45 min", "acc") == "45 минут"
