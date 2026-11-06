"""The parking card in the driver's language, per response (QA 2026-11-05): the English card showed Russian lines
after a Russian request for the same place, because localize_parking translated the cached verdict in place."""
import re

import pytest

from models import NearbyLot, ParkingCheck, ParkingSpot, Place
from services import i18n, parking, parking_store, voice

PLACE = Place(id="test-park-lang", name="Pilot Travel Center", category="truck_stop", lat=38.61234, lng=-121.51234)
EN = ParkingCheck(
    verdict="unlikely", label="Unlikely to fit a truck (satellite check)",
    reason="The lot is small and tight; there's a lot with trailers to the north, looks like a public lot.",
    source="satellite", image_path="/satellite?lat=38.61234&lng=-121.51234",
    entry_tip="Use the wide south driveway", park_tip="Park along the east side near existing trailers",
    spots=[ParkingSpot(kind="park", x=0.6, y=0.4, lat=38.6124, lng=-121.5122)],
    nearby=NearbyLot(direction="north", kind="public lot"),
)
EN_JSON = EN.model_dump(mode="json")
TEXT = ("label", "reason", "entry_tip", "park_tip")
# What the model says for the vision-written lines (the catalog covers the rest).
MODEL_RU = {
    "The lot is small and tight": "Площадка маленькая и тесная",
    "Use the wide south driveway": "Заезжай через широкий южный въезд",
    "Park along the east side near existing trailers": "Вставай вдоль восточной стороны рядом с уже стоящими трейлерами",
    "Park along the east side near existing trailers.": "Вставай вдоль восточной стороны рядом с уже стоящими трейлерами.",
}
EN_REPLY = ("Heads up, from the satellite it looks tight for a rig. There's a lot with trailers to the north, looks like a "
            "public lot. To get in: Use the wide south driveway. Park along the east side near existing trailers.")


@pytest.fixture
def cached(fake_llm, monkeypatch):
    """An English verdict in the cache for PLACE, the model's translations canned, no satellite, no TTS."""
    for en, ru in MODEL_RU.items():
        fake_llm.on("^" + re.escape(en) + "$", ru)

    async def no_paid(*a, **k):
        raise AssertionError("a paid call: the verdict is cached")

    async def no_tts(*a, **k):
        return None

    monkeypatch.setattr(parking, "fetch_satellite", no_paid)
    monkeypatch.setattr(parking, "_ask_vision", no_paid)
    monkeypatch.setattr(voice, "synthesize", no_tts)
    k = parking._key(PLACE.lat, PLACE.lng)
    parking._put(parking._verdict_cache, k, EN.model_copy(deep=True))
    yield k
    parking._verdict_cache.pop(k, None)


def _russian(card: dict) -> None:
    for f in TEXT:
        assert re.search(r"[А-Яа-я]", card[f]), (f, card[f])
        assert not re.search(r"\b[a-z]{3,}\b", card[f]), f"English left in {f}: {card[f]!r}"


@pytest.mark.parametrize("order", [("ru", "en"), ("en", "ru"), ("ru", "en", "ru", "en")])
def test_parking_card_follows_each_requests_language(api_client, cached, order):
    for code in order:
        r = api_client.post("/api/parking", json={"place": PLACE.model_dump(mode="json")}, headers={"X-Lang": code})
        assert r.status_code == 200, r.text
        card = r.json()
        if code == "en":
            assert card == EN_JSON  # byte-identical English, untouched by an earlier Russian request
        else:
            _russian(card)
            assert card["label"] == "Скорее всего, фура не встанет (по спутнику)"
            assert card["reason"] == ("Площадка маленькая и тесная; есть площадка с трейлерами к северу, "
                                      "похоже, общая стоянка.")
    assert parking._verdict_cache[cached].model_dump(mode="json") == EN_JSON  # the cache stays English


def test_spoken_parking_answer_follows_the_language(api_client, cached):
    place = {"place": PLACE.model_dump(mode="json"), "wait_s": 1}
    ru = api_client.post("/api/parking/answer", json=place, headers={"X-Lang": "ru"}).json()
    assert ru["reply"] == ("Внимание, по спутнику там тесно для фуры. Есть площадка с трейлерами к северу, похоже, "
                           "общая стоянка. Как заехать: Заезжай через широкий южный въезд. Вставай вдоль восточной стороны "
                           "рядом с уже стоящими трейлерами.")
    _russian(ru["parking"])
    en = api_client.post("/api/parking/answer", json=place, headers={"X-Lang": "en"}).json()
    assert en["reply"] == EN_REPLY
    assert en["parking"] == EN_JSON


def test_every_code_built_card_line_has_a_russian_template():
    """Labels and reasons services/parking.py writes itself, and the nearby-lot clause in every direction."""
    lines = [parking.town_check().label, parking.town_check().reason, *parking.LISTED[1:],
             "Likely fits a truck (satellite check)", "Unlikely to fit a truck (satellite check)",
             "Truck parking unclear", "Truck parking listed, not visible on satellite",
             "Satellite image unavailable.", "Couldn't read the satellite image.", parking.quota.SAT_SKIPPED]
    for d in ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"):
        for kind in ("public lot", "private yard", "unclear"):
            p = NearbyLot(direction=d, kind=kind).phrase
            lines += [f"{p[0].upper()}{p[1:]}.", f"{p}."]
    for line in lines:
        got = i18n.translate(line, "ru")
        assert not re.search(r"\b[a-z]{3,}\b", got), f"no ru template for {line!r}: {got!r}"


def test_a_verdict_saved_in_russian_is_not_loaded(tmp_path, monkeypatch):
    """Rows a Russian request wrote before the fix: their English is gone, so the place is checked again."""
    import json
    monkeypatch.setattr(parking_store, "DIR", tmp_path)
    monkeypatch.setattr(parking_store, "_mem", {})
    ru = EN.model_copy(update={"label": "Скорее всего, фура не встанет (по спутнику)"})
    (tmp_path / "a.json").write_text(json.dumps({"key": "1.00000,2.00000", "check": ru.model_dump(mode="json")}))
    (tmp_path / "b.json").write_text(json.dumps({"key": "3.00000,4.00000", "check": EN_JSON}))
    assert set(parking_store.load_all()) == {"3.00000,4.00000"}


def _model_calls(fake_llm) -> list[str]:
    return [text for system, text, _m in fake_llm.calls if system.startswith("Translate this line")]


@pytest.mark.parametrize("line", [
    "Alright. Route to Reno is set, 2 h 52 min, about 132 miles.",
    "I found Pilot, Nevada, about 300 miles away. Is that the one?",
    "Got it, I'll remember you don't like the 49er.",
])
def test_place_names_never_call_the_model(fake_llm, line):
    """{x|tr} holds names: the catalog line is enough, no model round trip on the route confirmation (#126 review)."""
    from conftest import run
    got = run(i18n.localize(line, "ru"))
    assert _model_calls(fake_llm) == []
    assert re.search(r"[А-Яа-я]", got)


def test_parking_card_model_calls_are_paid_once(api_client, cached, fake_llm):
    """First Russian card: one call per model-written text, all at once. The repeat and the spoken answer reuse
    them: the tips are not translated twice."""
    body = {"place": PLACE.model_dump(mode="json")}
    api_client.post("/api/parking", json=body, headers={"X-Lang": "ru"})
    assert sorted(_model_calls(fake_llm)) == sorted(["The lot is small and tight", "Use the wide south driveway",
                                                     "Park along the east side near existing trailers"])
    fake_llm.calls.clear()
    api_client.post("/api/parking", json=body, headers={"X-Lang": "ru"})
    api_client.post("/api/parking/answer", json={**body, "wait_s": 1}, headers={"X-Lang": "ru"})
    assert _model_calls(fake_llm) == []


def test_slow_model_leaves_the_card_to_the_catalog(api_client, cached, fake_llm, monkeypatch):
    """Over the card budget the catalog's lines are Russian and the model's stay English, never a hang."""
    import asyncio
    from services import llm

    async def slow(system, text, *a, **k):
        await asyncio.sleep(0.5)
        return "медленно"

    monkeypatch.setattr(llm, "complete", slow)
    monkeypatch.setattr(i18n, "CARD_BUDGET_S", 0.05)
    card = api_client.post("/api/parking", json={"place": PLACE.model_dump(mode="json")}, headers={"X-Lang": "ru"}).json()
    assert card["label"] == "Скорее всего, фура не встанет (по спутнику)"
    assert card["park_tip"] == EN.park_tip
    assert card["reason"] == "The lot is small and tight; есть площадка с трейлерами к северу, похоже, общая стоянка."


def test_wind_from_a_three_part_direction():
    line = "Reno at 3 pm: 63 degrees and sunny, wind 15 miles an hour from the north-northwest."
    assert i18n.translate(line, "ru") == ("Reno в 15:00: 63 градуса по Фаренгейту, солнечно, ветер 15 миль в час, "
                                          "северо-северо-западный.")


def test_no_double_period_after_a_model_translated_tip():
    got = i18n.translate("To get in: Use the wide south driveway.", "ru",
                         fallback=lambda s, c: "Заезжай через широкий южный въезд.")
    assert got == "Как заехать: Заезжай через широкий южный въезд."
