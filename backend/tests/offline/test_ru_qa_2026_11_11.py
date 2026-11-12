"""The second Russian QA pass, 2026-11-11 (vault: «TruckBuddy — QA русской версии, второй проход: ответы копилота»),
server side: a weather watch reads as a noun in every weather line, the accusative after «через», the break reminder
for one hour, arrival at "your destination" on a side, HERE incident descriptions, Latin words a model translation
left behind, and "где встать на ночь" as a truck stop."""
import re

import pytest
from conftest import run

from models import AgentRequest, Incident, Maneuver, RouteInfo
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
    ("{e} near Donner Pass around 3:30 pm and 2 more weather alerts along the route.",
     "{R} в районе Donner Pass около 15:30 и ещё 2 предупреждения по маршруту."),
    ("The way via I-80 only runs into a {e}. Say 'take the other way' if you want it.",
     "На пути через I-80 только {r}. Скажи «давай другим путём», если хочешь."),
    ("The way via I-80 only runs into a {e}, about 12 min longer. Say 'take the other way' if you want it.",
     "На пути через I-80 только {r}, примерно на 12 минут дольше. Скажи «давай другим путём», если хочешь."),
    ("The way via I-80 only runs into a {e} and takes about the same time. Say 'take the other way' if you want it.",
     "На пути через I-80 только {r} и по времени примерно так же. Скажи «давай другим путём», если хочешь."),
    ("The way via I-80 misses it and takes about the same time.", "Путь через I-80 это обходит и по времени примерно так же."),
    ("Here's another way via I-80 and CA-89, 4 h 29 min, 204 miles, about 1 h 40 min longer, and it misses the {e}.",
     "Вот другой путь через I-80 и CA-89, 4 часа 29 минут, 204 мили, примерно на 1 час 40 минут дольше, и {r} обходит стороной."),
    ("Here's another way via I-80, 4 h 29 min, 204 miles, about 1 h 40 min longer, but it runs into a {e}.",
     "Вот другой путь через I-80, 4 часа 29 минут, 204 мили, примерно на 1 час 40 минут дольше, но там {r}."),
]


def test_every_event_template_is_exercised():
    """A new template with {event} must get a line in EVENT_LINES, so a watch is checked in it too."""
    tpls, _words = i18n.catalog("ru")
    with_event = [t for t in tpls if "{event" in t.target]
    assert len(with_event) == 13, [t.en for t in with_event]
    for t in with_event:
        assert "{event|nom}" in t.target, t.target  # the only form the words table holds for an event


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

def test_incident_descriptions_from_the_qa_routes_are_translated():
    """The two descriptions the QA run logged as `i18n miss ru`, as routing._incident stores them (no last period)."""
    out = _localized(_route(incidents=[
        Incident(type="other", closed=False, description="Major event", index=1),
        Incident(type="construction", closed=False, description="Road construction. Roadway reduced to two lanes", index=2),
        Incident(type="accident", closed=False, description="Accident. Right lane blocked", index=3),
        Incident(type="construction", closed=False, description="Roadway reduced to one lane", index=4)]))
    assert [(i.type, i.description) for i in out.incidents] == [
        ("Происшествие", "Крупное мероприятие"), ("Ремонт дороги", "Ремонт дороги. Дорога сужена до двух полос"),
        ("Авария", "Авария. Правая полоса перекрыта"), ("Ремонт дороги", "Дорога сужена до одной полосы")]


def test_unknown_incident_description_stays_english_whole():
    out = _localized(_route(incidents=[
        Incident(type="other", closed=False, description="Road construction. Pilot car operations in the canyon", index=1),
        Incident(type="construction", closed=False, description="Roadway reduced to two narrow lanes", index=2)]))
    # one sentence the catalog lacks keeps the whole description English: the app does not say such a description
    assert [(i.type, i.description) for i in out.incidents] == [
        ("Происшествие", "Road construction. Pilot car operations in the canyon"),
        ("Ремонт дороги", "Roadway reduced to two narrow lanes")]


def test_recorded_here_incident_descriptions_all_translate():
    """Every incident description in the recorded HERE responses has a template."""
    import json
    from pathlib import Path
    seen = set()
    for path in (Path(__file__).resolve().parents[1] / "fixtures" / "here").glob("*.json"):
        seen.update(re.findall(r'"description":\s*"([^"]+)"', json.dumps(json.loads(path.read_text()), ensure_ascii=False)))
    assert {"Road construction", "Major road construction"} <= seen
    for text in seen:
        _got, misses = _ru(text.rstrip("."))
        assert not misses, text
