"""The copilot speaks up about the weather ahead on its own (weather plan, PR D; Figma V1)."""
from conftest import run
from models import NudgeRequest, Place, RouteInfo, WeatherAhead, WeatherAlert
from services import nudges

RENO = Place(id="reno", name="Reno, NV", category="destination", lat=39.53, lng=-119.81)


def _route(**kw):
    base = dict(geometry=[[38.58, -121.49], [39.53, -119.81]], distance_m=210000, duration_s=10320, distance_mi=130,
                duration_min=172, summary="x", source="here")
    base.update(kw)
    return RouteInfo(**base)


def _req(wa, **kw):
    return NudgeRequest(driving=True, active_place=RENO, lat=38.58, lng=-121.49, session_id="wn", weather_ahead=wa, **kw)


def test_lines():
    assert nudges.weather_line(WeatherAhead(key="ice-5", kind="ice", place="Truckee, CA", minutes_ahead=42, temp_f=31)) == \
        "Heads up. Ice risk near Truckee in about 40 minutes, 31 degrees on a wet road. Bridges ice first."
    assert "Gusts to 60 miles an hour near Reno" in nudges.weather_line(WeatherAhead(key="w", kind="wind", place="Reno, NV", minutes_ahead=55, gust_mph=60))
    assert nudges.weather_line(WeatherAhead(key="s", kind="snow", place="Donner Pass", minutes_ahead=20, event="Winter Storm Warning")) == \
        "Heads up. Winter Storm Warning near Donner Pass in about 20 minutes."
    assert nudges.weather_line(WeatherAhead(key="c", kind="cloud", minutes_ahead=10)) is None


def test_once_per_point_and_only_within_the_hour():
    sess: dict = {}
    far = WeatherAhead(key="ice-5", kind="ice", place="Truckee", minutes_ahead=90, temp_f=31)
    assert run(nudges.compute(_req(far), sess, None)) == []
    near = far.model_copy(update={"minutes_ahead": 45})
    out = run(nudges.compute(_req(near), sess, None))
    assert [n.kind for n in out] == ["weather"]
    assert run(nudges.compute(_req(near), sess, None)) == []  # said once
    other = WeatherAhead(key="wind-8", kind="wind", place="Reno", minutes_ahead=50, gust_mph=55)
    assert [n.kind for n in run(nudges.compute(_req(other), sess, None))] == ["weather"]


def test_not_while_parked():
    wa = WeatherAhead(key="ice-5", kind="ice", place="Truckee", minutes_ahead=30, temp_f=31)
    assert run(nudges.compute(NudgeRequest(driving=False, lat=38.58, lng=-121.49, weather_ahead=wa), {}, None)) == []


def test_flag_off(monkeypatch):
    monkeypatch.setenv("NUDGE_WEATHER", "0")
    wa = WeatherAhead(key="ice-5", kind="ice", place="Truckee", minutes_ahead=30, temp_f=31)
    assert run(nudges.compute(_req(wa), {}, None)) == []


def test_alert_offers_the_way_around_the_router_found():
    alert = WeatherAlert(id="a1", event="Winter Storm Warning", severity="Severe", headline="h", area="Tahoe", index=1)
    clear = _route(duration_s=14200, duration_min=237, labels=["CA-70"], weather_known=True)
    cur = _route(weather=[alert], weather_known=True, alternatives=[clear])
    sess = {"alternatives": {"current": cur}}
    wa = WeatherAhead(key="snow-4", kind="snow", place="Donner Pass", minutes_ahead=40, event="Winter Storm Warning")
    [n] = run(nudges.compute(_req(wa), sess, None))
    assert n.text.startswith("Heads up. Winter Storm Warning near Donner Pass in about 40 minutes. The way via CA-70 misses it")
    assert "take the other way" in n.text


def test_no_alert_no_detour_claim():
    sess = {"alternatives": {"current": _route()}}
    wa = WeatherAhead(key="fog-2", kind="fog", place="Lodi", minutes_ahead=15, vis_mi=0.4)
    [n] = run(nudges.compute(_req(wa), sess, None))
    assert n.text == "Heads up. Fog near Lodi in about 15 minutes, visibility under a mile."


def test_every_warning_kind_has_a_line():
    """Review 2026-11-11: a warn point with no line was silent and held back the ones after it."""
    cases = [
        WeatherAhead(key="a", kind="ice", place="Truckee", minutes_ahead=30),  # no temperature
        WeatherAhead(key="b", kind="snow", place="Truckee", minutes_ahead=30, reasons=["low_visibility"]),
        WeatherAhead(key="c", kind="snow", place="Truckee", minutes_ahead=30, reasons=["gusts"], gust_mph=58),
        WeatherAhead(key="d", kind="fog", place="Lodi", minutes_ahead=30, reasons=["low_visibility"]),
        WeatherAhead(key="e", kind="wind", place="Reno", minutes_ahead=30, reasons=["gusts"], gust_mph=60),
        WeatherAhead(key="f", kind="cloud", place="Reno", minutes_ahead=30, event="High Wind Warning"),
    ]
    for wa in cases:
        assert nudges.weather_line(wa), wa.key
