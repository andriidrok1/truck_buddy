"""Conversation memory without Postgres: the SQLite store behind the same functions and the /api/memory routes."""

import pytest
from conftest import run

from services import memory


@pytest.fixture
def sqlite_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    monkeypatch.setenv("MEMORY_SQLITE_PATH", str(tmp_path / "mem.sqlite"))
    run(memory.close())
    yield tmp_path / "mem.sqlite"
    run(memory.close())


def test_store_selection(monkeypatch):
    monkeypatch.delenv("MEMORY_STORE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert memory.store() == "sqlite"
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    assert memory.store() == "pg"
    monkeypatch.setenv("MEMORY_STORE", "off")
    assert memory.store() == "off"
    monkeypatch.setenv("MEMORY_STORE", "postgres")
    assert memory.store() == "pg"


def test_turns_facts_and_clear(sqlite_store):
    run(memory.add("d1", [("driver", "find a truck stop"), ("copilot", "Pilot in Lodi, 12 miles.")]))
    run(memory.add("d1", [("driver", "thanks"), ("copilot", "")]))  # empty text is skipped
    run(memory.add("d2", [("driver", "other driver")]))
    turns = run(memory.recent("d1", 10))
    assert [t["text"] for t in turns] == ["find a truck stop", "Pilot in Lodi, 12 miles.", "thanks"]
    assert turns[0]["role"] == "driver" and turns[0]["at"].endswith("+00:00")
    assert run(memory.recent("d1", 2)) == turns[-2:]  # limit keeps the newest, oldest first
    assert run(memory.count_turns("d1")) == 3 and run(memory.count_turns("d2")) == 1
    assert run(memory.get_facts("d1")) is None
    run(memory.set_facts("d1", {"chains_liked": ["Pilot"]}, 3))
    run(memory.set_facts("d1", {"chains_liked": ["Pilot", "TA"]}, 5))  # upsert
    stored = run(memory.get_facts("d1"))
    assert stored["facts"] == {"chains_liked": ["Pilot", "TA"]} and stored["turns_seen"] == 5
    assert stored["updated_at"].tzinfo is not None
    assert run(memory.clear_facts("d1")) == 1 and run(memory.get_facts("d1")) is None
    assert run(memory.clear("d1")) == 3
    assert run(memory.recent("d1", 10)) == [] and run(memory.count_turns("d2")) == 1
    assert sqlite_store.exists()


def test_add_later_is_visible_to_the_next_read(sqlite_store):
    async def flow():
        memory.add_later("d3", [("driver", "hello"), ("copilot", "Hi.")])
        return await memory.recent("d3", 10)  # waits for the save in flight
    assert [t["text"] for t in run(flow())] == ["hello", "Hi."]


def test_off_store_raises_and_warm_is_quiet(monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "off")
    with pytest.raises(RuntimeError):
        run(memory.recent("x", 5))
    run(memory.warm())  # no exception


def test_memory_routes_with_sqlite(api_client, sqlite_store, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    r = api_client.get("/api/memory")
    assert r.status_code == 200 and r.json() == {"turns": []}
    # a turn saved by the agent path shows up on the screen
    run(memory.add(_driver_of(api_client), [("driver", "hi"), ("copilot", "Hello.")]))
    r = api_client.get("/api/memory")
    assert [t["text"] for t in r.json()["turns"]] == ["hi", "Hello."]
    r = api_client.delete("/api/memory")
    assert r.status_code == 200 and r.json()["deleted"] == 2
    assert api_client.get("/api/memory").json() == {"turns": []}


def _driver_of(client) -> str:
    """The driver id inside the device token the test client sends."""
    import base64
    import json
    tok = client.headers["Authorization"].split()[1]
    payload = tok.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["sub"]
