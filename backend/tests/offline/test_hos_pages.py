"""Bug 8 (review 2026-11-02): with a fleet key the driver is asked for by `driver_ids[]`. If Motive ignores the
filter, the answer is the company's first page and a driver further down was "not in the answer". The pages are
walked until the driver turns up, up to MAX_PAGES."""
import httpx
import pytest

from services import hos

from conftest import run


def _user(uid, drive=3600):
    return {"user": {"id": uid, "first_name": f"U{uid}"}, "available_time": {"drive": drive, "shift": drive, "cycle": drive}}


def _pages(total_pages, where):
    """A company of `total_pages` pages of 2 users; the wanted user 42 sits on page `where` (None = nowhere)."""
    def fn(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page_no", "1"))
        users = [_user(page * 10), _user(page * 10 + 1)]
        if page == where:
            users.append(_user(42, 5400))
        return httpx.Response(200, json={"users": users, "pagination": {"per_page": 100, "page_no": page, "total": total_pages * 100}})
    return fn


def test_the_driver_is_found_past_the_first_page(fake_http):
    fake_http.add("api.gomotive.com", "/v1/available_time", fn=_pages(3, 3))
    h = run(hos.available_time("fleet-key", "42"))
    assert h.drive_min == 90 and h.driver == "U42"
    pages = [c.url.params.get("page_no") for c in fake_http.calls_to("api.gomotive.com")]
    assert pages == ["1", "2", "3"]
    assert all(c.url.params.get("driver_ids[]") == "42" and c.url.params.get("per_page") == "100"
               for c in fake_http.calls_to("api.gomotive.com"))


def test_a_driver_on_no_page_is_down_not_somebody_else(fake_http):
    fake_http.add("api.gomotive.com", "/v1/available_time", fn=_pages(2, None))
    with pytest.raises(hos.HosDown):
        run(hos.available_time("fleet-key", "42"))
    assert len(fake_http.calls_to("api.gomotive.com")) == 2


def test_the_walk_stops_at_max_pages(fake_http):
    fake_http.add("api.gomotive.com", "/v1/available_time", fn=_pages(50, 50))
    with pytest.raises(hos.HosDown):
        run(hos.available_time("fleet-key", "42"))
    assert len(fake_http.calls_to("api.gomotive.com")) == hos.MAX_PAGES


def test_a_personal_key_is_one_call(fake_http):
    fake_http.add("api.gomotive.com", "/v1/available_time", fn=_pages(3, None))
    assert run(hos.available_time("own-key")).driver == "U10"
    assert len(fake_http.calls_to("api.gomotive.com")) == 1
