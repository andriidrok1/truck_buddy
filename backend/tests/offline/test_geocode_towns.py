"""A town named with its state is the town, not a same-named street or business nearer by; a chain name is
never a hamlet of that name."""
import httpx
from conftest import run

from services import geocode
from services.geocode import chain_query, town_query

FH = (33.6117, -111.7174)  # Fountain Hills, AZ


def _item(rtype, title, lat, lng, score=1.0, label=None):
    return {"resultType": rtype, "title": title, "position": {"lat": lat, "lng": lng}, "id": f"id-{title}",
            "address": {"label": label or title}, "scoring": {"queryScore": score}}


def test_town_and_chain_detection():
    assert town_query("Payson, AZ") and town_query("Payson, Arizona") and town_query("Truth or Consequences, New Mexico")
    assert not town_query("Payson") and not town_query("480 Truck Inn Way, Fernley NV") and not town_query("Pilot, Fernley NV")
    assert not town_query("Pilot, NV") and not town_query("Payson, AZ, United States")
    assert chain_query("Pilot") and chain_query("the Love's") and chain_query("TA")
    assert not chain_query("Pilot, Fernley NV") and not chain_query("Reno")


def test_a_town_with_its_state_beats_the_street_and_the_business(fake_http):
    def geo(request: httpx.Request):
        if request.url.params.get("types") == "city":
            return httpx.Response(200, json={"items": [_item("locality", "Payson, AZ, United States", 34.2309, -111.3251)]}, request=request)
        return httpx.Response(200, json={"items": [
            _item("place", "Cave Creek, Az", 33.62, -111.90, label="Cave Creek, Az, 15333 N Pima Rd, Scottsdale, AZ"),
            _item("street", "Payson, Phoenix, AZ 85086, United States", 33.84, -112.11)]}, request=request)
    fake_http.add("geocode.search.hereapi.com", "/v1/geocode", fn=geo)
    fake_http.add("discover.search.hereapi.com", "/v1/discover", json={"items": []})
    found = run(geocode.geocode("Payson, AZ", *FH))
    assert len(found) == 1 and found[0].name.startswith("Payson, AZ") and found[0].id.startswith("here-town-")
    assert abs(found[0].lat - 34.2309) < 1e-6
    assert any(c.url.params.get("types") == "city" for c in fake_http.calls_to("geocode.search.hereapi.com"))


def test_a_bare_chain_name_is_the_business_not_the_hamlet(fake_http):
    fake_http.add("geocode.search.hereapi.com", "/v1/geocode", json={"items": [
        _item("locality", "Pilot, Wells, NV, United States", 40.82, -114.24)]})
    fake_http.add("discover.search.hereapi.com", "/v1/discover", json={"items": [
        _item("place", "Pilot", 38.65, -121.38, label="Pilot, 1001 Truck Stop Way, Sacramento, CA")]})
    found = run(geocode.geocode("Pilot", 38.5816, -121.4944))
    assert len(found) == 1 and found[0].lat == 38.65 and not found[0].id.startswith("here-town-")
    assert not any(c.url.params.get("types") == "city" for c in fake_http.calls_to("geocode.search.hereapi.com"))


def test_a_plain_query_asks_once_as_before(fake_http):
    fake_http.add("geocode.search.hereapi.com", "/v1/geocode", json={"items": [_item("locality", "Reno, NV, United States", 39.53, -119.81)]})
    fake_http.add("discover.search.hereapi.com", "/v1/discover", json={"items": []})
    found = run(geocode.geocode("Reno", 38.5816, -121.4944))
    assert found and found[0].name.startswith("Reno")
    assert len(fake_http.calls_to("geocode.search.hereapi.com")) == 1
