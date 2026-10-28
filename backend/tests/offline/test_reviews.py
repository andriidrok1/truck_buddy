"""Google reviews on places and the spoken gist: parsing, attribution, caching, the reviews intent."""
import httpx
from conftest import fixture_json, run

from models import AgentRequest, Place, Review
from services import agent, reviews
from services.google_places import GooglePlacesProvider, field_mask, reviews_from

GOOGLE = fixture_json("google/truck_stop_sac")
SAC = {"lat": 38.5816, "lng": -121.4944}


def test_reviews_from_google_shape():
    out = reviews_from(GOOGLE["places"][0]["reviews"])
    assert [r.author for r in out] == ["Marcus T.", "Dee", "J. Ramirez"]  # the empty one is skipped
    assert out[0].rating == 5 and out[0].uri == "https://www.google.com/maps/reviews/r1"
    assert out[0].time == "2026-10-12T03:41:00Z"
    assert out[2].text == "Decent stop. Scale on site." and out[2].uri == "https://www.google.com/maps/contrib/1003"
    assert reviews_from(None) == [] and reviews_from("x") == [] and reviews_from([1, {"text": None}]) == []


def test_long_review_text_is_capped_and_max_five_kept():
    long = {"text": {"text": "word " * 200}, "authorAttribution": {"displayName": "A"}, "rating": 4.0}
    r = reviews_from([long] * 7)
    assert len(r) == 5 and len(r[0].text) <= 301 and r[0].text.endswith("…") and r[0].rating == 4
    assert reviews_from([{"text": "plain string works too", "authorAttribution": {}}])[0].author == "A Google user"


def test_field_mask_follows_the_flag(monkeypatch):
    assert "places.reviews" in field_mask().split(",")
    monkeypatch.setenv("PLACE_REVIEWS", "0")
    assert "places.reviews" not in field_mask()


def test_provider_puts_reviews_on_the_place(monkeypatch, fake_http):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-google")
    fake_http.add("places.googleapis.com", "/v1/places:searchText", json=GOOGLE)
    res = run(GooglePlacesProvider().search("truck_stop", SAC["lat"], SAC["lng"]))
    assert fake_http.calls[0].headers["X-Goog-FieldMask"].endswith(",places.reviews")
    by_id = {p.id: p for p in res.places}
    assert len(by_id["ChIJ-pilot-west-sac"].reviews) == 3 and by_id["ChIJ-loves-sac"].reviews == []
    assert "ChIJ-far-reno-flyingj" not in by_id  # outside the radius
    assert by_id["ChIJ-pilot-west-sac"].diesel_price == 5.39


def _pilot() -> Place:
    return Place(id="p1", name="Pilot Travel Center", category="truck_stop", lat=38.58, lng=-121.55, rating=4.1,
                 rating_count=1832, reviews=reviews_from(GOOGLE["places"][0]["reviews"]))


def test_gist_prompt_carries_the_reviews_and_is_cached(fake_llm):
    reviews._gists.clear()
    fake_llm.on(r"^Place: Pilot", '"Plenty of parking and clean showers, but the lot fills up early"')
    place = _pilot()
    g1 = run(reviews.gist(place, now=1000.0))
    assert g1 == "Plenty of parking and clean showers, but the lot fills up early."
    assert "Showers were clean" in fake_llm.calls[0][1] and "(5 stars)" in fake_llm.calls[0][1]
    assert "Marcus" not in fake_llm.calls[0][0]  # the prompt tells the model not to use names; we don't need them
    g2 = run(reviews.gist(place, now=1000.0 + 3600))
    assert g2 == g1 and len(fake_llm.calls) == 1  # cached
    g3 = run(reviews.gist(place, now=1000.0 + reviews.GIST_TTL_S + 1))
    assert g3 == g1 and len(fake_llm.calls) == 2  # expired, re-asked


def test_speech_variants(fake_llm):
    reviews._gists.clear()
    fake_llm.on(r"^Place: Pilot", "Good parking, slow showers.")
    assert run(reviews.speech(_pilot())) == "Drivers say: Good parking, slow showers."
    reviews._gists.clear()
    fake_llm.rules.clear()  # no canned reply -> the fake raises, like a model outage
    assert run(reviews.speech(_pilot())) == "Drivers rate it 4.1 out of 5, but I can't read the reviews right now."
    none = _pilot().model_copy(update={"reviews": []})
    assert run(reviews.speech(none)) == "No driver reviews there yet."


def test_reviews_intent_in_the_dialog(agent_sandbox):
    reviews._gists.clear()
    agent_sandbox.llm.on(r"^Place: Pilot", "Lots of parking, showers can take a while.")
    agent_sandbox.llm.on(r"drivers say", {"intent": "reviews", "place_index": None})
    pilot = _pilot()
    agent_sandbox.places_provider.places = [pilot]
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="what do drivers say about it?", session_id="rv", active_place=pilot,
                                       **SAC), None))
    assert res.status == "chat" and res.reply == "Drivers say: Lots of parking, showers can take a while."
    assert res.place and res.place.id == pilot.id and len(res.place.reviews) == 3

    res = run(prov.handle(AgentRequest(text="what do drivers say?", session_id="nothing", **SAC), None))
    assert res.status == "chat" and res.reply == agent.WHICH_PLACE


def test_context_lists_ratings(agent_sandbox):
    pilot = _pilot()
    sess = {"history": [], "places": [pilot]}
    ctx = agent_sandbox.provider()._context(AgentRequest(text="hi", **SAC), sess)
    assert "4.1 stars (1832 reviews)" in ctx


def test_review_model_is_what_the_app_gets():
    r = Review(author="A", text="t")
    assert r.model_dump() == {"author": "A", "rating": None, "text": "t", "uri": None, "time": None}
    assert isinstance(httpx, object)  # keep the import honest for future transport tests
