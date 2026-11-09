"""HERE changes its response shapes without notice: a part of a route that can't be read is dropped, never the
route (2026-11-08: maxWeight became {"value", "type"} and every route with a weight restriction failed)."""
import copy

from conftest import fixture_json

from models import TruckProfile
from services import routing

DATA = fixture_json("here/route_sac_reno")
WEIGHT = {"title": "Violated vehicle restriction.", "code": "violatedVehicleRestriction", "severity": "critical",
          "details": [{"type": "restriction", "cause": "Route violates vehicle restriction: weight limit of 3628 kg",
                       "maxGrossWeight": 3628, "maxWeight": {"value": 3628, "type": "unknown"}}]}


def _with(notices=None, **section):
    d = copy.deepcopy(DATA)
    s = d["routes"][0]["sections"][0]
    if notices is not None:
        s["notices"] = notices
    s.update(section)
    return d


def test_num_reads_every_shape():
    assert routing._num(3628) == 3628 and routing._num("4.5") == 4.5 and routing._num({"value": 7, "type": "x"}) == 7
    assert routing._num(None) is None and routing._num({"type": "x"}) is None and routing._num(True) is None


def test_weight_limit_as_an_object_is_named():
    r = routing._here_parse(_with([WEIGHT]), TruckProfile())
    assert r.restricted == "weight" and any("7,998 lb" in n for n in r.notices)


def test_admin_wide_limit_as_an_object():
    legal = {"code": "violatedAdminWideVehicleRestriction", "details": [{"state": "NJ", "maxWeight": {"value": 36287}}]}
    r = routing._here_parse(_with([legal]), TruckProfile())
    assert r.restricted == "legal" and any("80,000 lb" in n or "79,999 lb" in n for n in r.notices)


def test_an_unreadable_part_is_dropped_not_the_route():
    bad = _with([{"code": "violatedVehicleRestriction", "details": "garbage"}],
                tolls=[{"fares": "garbage"}], spans=[{"offset": "x", "maxSpeed": {"weird": 1}}])
    r = routing._here_parse(bad, TruckProfile())
    assert r.source == "here" and r.geometry and r.maneuvers and r.distance_mi > 100


def test_no_toll_notice_when_tolls_were_not_avoided():
    toll = [{"code": "tollTransponder", "severity": "info",
             "title": "Route goes through toll booth that requires transponder."}]  # live NJ->Boston, 2026-11-08
    assert not any("Tolls" in n for n in routing._here_parse(_with(toll), TruckProfile()).notices)
    avoid = [{"code": "violatedAvoidTollRoad", "severity": "critical"}]
    assert any("Tolls" in n for n in routing._here_parse(_with(avoid), TruckProfile(avoid_tolls=True)).notices)
