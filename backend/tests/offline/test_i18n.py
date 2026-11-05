"""services.i18n: the request language and template translation of what the copilot says."""
import json
import re

import pytest
from conftest import run

from services import i18n

CAT = {
    "templates": [
        {"en": "Route to {name} is set, {time}, about {mi} miles.",
         "ru": "Маршрут до {name} готов, {time|time}, около {mi|plural:мили,миль,миль}."},
        {"en": "I couldn't find a {what} ahead on your route.", "ru": "Не нашёл {what|acc} впереди по маршруту."},
        {"en": "Alright.", "ru": "Хорошо."},
        {"en": "Heads up, {line}", "ru": "Внимание: {line|tr}"},
        {"en": "the road is closed", "ru": "дорога закрыта"},
    ],
    "words": {"truck stop": {"nom": "стоянка для фур", "acc": "стоянку для фур"}},
}


@pytest.fixture
def ru(tmp_path, monkeypatch):
    (tmp_path / "ru.json").write_text(json.dumps(CAT, ensure_ascii=False))
    monkeypatch.setattr(i18n, "CATALOG_DIR", tmp_path)
    i18n.catalog.cache_clear()
    yield
    i18n.catalog.cache_clear()


def test_lang_codes():
    assert i18n.norm("ru-RU") == "ru" and i18n.norm("uk_UA") == "uk" and i18n.norm("de") == "en" and i18n.norm(None) == "en"


def test_forms():
    assert [i18n.plural_ru(n, "миля", "мили", "миль") for n in (1, 2, 5, 11, 21, 22, 112, 2.5)] == \
        ["миля", "мили", "миль", "миль", "миля", "мили", "миль", "мили"]
    assert i18n.time_ru("2 h 52 min") == "2 часа 52 минуты" and i18n.time_ru("45 min") == "45 минут"
    assert i18n.time_ru("1 h") == "1 час" and i18n.time_ru("soon") == "soon"


def test_whole_reply_and_sentence_runs(ru):
    assert i18n.translate("Route to Reno, NV is set, 2 h 52 min, about 132 miles.", "ru") == \
        "Маршрут до Reno, NV готов, 2 часа 52 минуты, около 132 миль."
    assert i18n.translate("Alright. I couldn't find a truck stop ahead on your route.", "ru") == \
        "Хорошо. Не нашёл стоянку для фур впереди по маршруту."
    assert i18n.translate("Heads up, the road is closed", "ru") == "Внимание: дорога закрыта"  # nested template


def test_numbers_never_change_and_misses_stay_english(ru):
    out = i18n.translate("Alright. Turn left on Main St in 500 feet.", "ru", fallback=lambda s, c: "Через 400 футов налево.")
    assert out == "Хорошо. Turn left on Main St in 500 feet."  # the fallback changed 500 -> 400: rejected
    out = i18n.translate("Alright. Turn left in 500 feet.", "ru", fallback=lambda s, c: "Через 500 футов налево.")
    assert out == "Хорошо. Через 500 футов налево."


def test_english_and_already_translated_pass_through(ru):
    assert i18n.translate("Alright.", "en") == "Alright."
    assert i18n.translate("Конечно, Pilot в двух милях.", "ru") == "Конечно, Pilot в двух милях."


def test_request_language_and_async_fallback(ru, monkeypatch):
    class _Llm:
        async def complete(self, system, user):
            return "Через 500 футов налево."

    from services import llm
    monkeypatch.setattr(llm, "complete", _Llm().complete)
    i18n.set_lang("ru")
    try:
        assert run(i18n.localize("Alright. Turn left in 500 feet.")) == "Хорошо. Через 500 футов налево."
    finally:
        i18n.set_lang("en")
    assert run(i18n.localize("Alright.")) == "Alright."


def test_clock_and_parts(ru, tmp_path):
    assert i18n.clock_ru("3:30 pm") == "15:30" and i18n.clock_ru("8 am") == "8:00" and i18n.clock_ru("12 pm") == "12:00"
    assert i18n.clock_ru("tomorrow 8:58 am") == "завтра в 8:58" and i18n.clock_ru("Thursday 6 am") == "в четверг в 6:00"
    assert i18n.clock_ru("soon") == "soon"
    cat = {"templates": [{"en": "{name}{off}, about {mi} miles out.", "ru": "{name}{off|part}, около {mi|plural:мили,миль,миль}."},
                         {"en": ", right off {street}", "ru": ", рядом с {street}", "part": True},
                         {"en": "{a} and {b}", "ru": "{a} и {b}", "part": True}]}
    (tmp_path / "ru.json").write_text(json.dumps(cat, ensure_ascii=False))
    i18n.catalog.cache_clear()
    assert i18n.translate("Pilot, right off N Virginia St, about 132 miles out.", "ru") == \
        "Pilot, рядом с N Virginia St, около 132 миль."
    assert i18n.translate("Pilot, about 1 miles out.", "ru") == "Pilot, около 1 мили."
    assert i18n.translate("Tea and cake", "ru") == "Tea and cake"  # a part never matches a whole line


# ---- the real catalog -----------------------------------------------------------------------------------------

def test_real_catalog_lines():
    i18n.catalog.cache_clear()
    cases = {
        "Alright, got one. Love's Travel Stop, right off N Virginia St, about 132 miles out.":
            "Так, есть один. Love's Travel Stop, рядом с N Virginia St, примерно в 132 милях.",
        "Alright. Route to Reno is set, 2 h 52 min, about 1 miles.":
            "Хорошо. Маршрут до Reno готов, 2 часа 52 минуты, около 1 мили.",
        "Okay, no stop at Pilot Travel Center. New total 2 h 52 min, 132 miles.":
            "Хорошо, без остановки в Pilot Travel Center. Всего теперь 2 часа 52 минуты, 132 мили.",
        "This route goes under a 13 ft 10 in clearance; your truck is 14 ft 1 in.":
            "На маршруте проезд высотой 13 футов 10 дюймов, а твой трак 14 футов 1 дюйм.",
        "Weather warning: High Wind Warning near Reno Area, you'd hit it around 3:30 pm, in effect till 8:58 pm.":
            "Погодное предупреждение: штормовое предупреждение о ветре в районе Reno Area, доедешь туда примерно в 15:30, "
            "действует до 20:58.",
        "You've got 4 hours 20 minutes of drive time, 9 hours on the shift, and 42 hours left on the cycle.":
            "За рулём осталось 4 часа 20 минут, на смене 9 часов, по циклу 42 часа.",
        "Your break is due in 15 minutes. There's a Gold Run Rest Area in 41 miles, want me to add it?":
            "Перерыв через 15 минут. Через 41 миля есть Gold Run Rest Area, добавить?",
    }
    for en, ru in cases.items():
        assert i18n.translate(en, "ru") == ru, en


def test_a_russian_turn_end_to_end(api_client, agent_sandbox):
    """lang=ru on /api/agent: a templated answer comes back in Russian, the place names stay as they are."""
    i18n.catalog.cache_clear()
    agent_sandbox.llm.on(r"трак-стоп|truck stop", {"intent": "find", "category": "truck_stop"})
    r = api_client.post("/api/agent", json={"text": "найди трак-стоп", "lat": 38.5816, "lng": -121.4944, "lang": "ru"})
    assert r.status_code == 200, r.text
    reply = r.json()["reply"]
    assert re.search(r"[А-Яа-я]", reply) and not re.search(r"\b(miles|about|right off)\b", reply), reply
    en = api_client.post("/api/agent", json={"text": "find a truck stop", "lat": 38.5816, "lng": -121.4944})
    assert en.status_code == 200 and not re.search(r"[А-Яа-я]", en.json()["reply"])  # no lang: English as before
