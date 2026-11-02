"""Schema migrations: numbered SQL files applied once each, recorded in schema_version, idempotent."""
import os
import sqlite3

import pytest
from conftest import run

from services import db, memory, trip_events


def test_migration_files_are_ordered_and_sqlite_twins_replace():
    pg = db.migration_files("pg")
    lite = db.migration_files("sqlite")
    assert [v for v, _ in pg] == sorted(v for v, _ in pg) and pg[0][0] == 1
    assert [v for v, _ in lite] == [v for v, _ in pg]  # every version exists in both dialects
    assert lite[0][1].parent.name == "sqlite" and pg[0][1].parent.name == "migrations"


def test_sqlite_migrates_once_and_is_idempotent(sqlite_store):
    run(db.migrate())  # the first connection migrates on open, so this call may find nothing pending
    assert run(db.migrate()) == []  # and nothing is pending afterwards
    con = sqlite3.connect(str(sqlite_store))
    versions = [r[0] for r in con.execute("SELECT version FROM schema_version ORDER BY version")]
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert versions == [v for v, _ in db.migration_files("sqlite")]
    assert {"conversation", "driver_facts", "trip_events", "schema_version"} <= tables
    # the stores work on the migrated schema (no CREATE IF NOT EXISTS of their own any more)
    run(memory.add("d", [("driver", "hi")]))
    run(trip_events.add("d", [{"kind": "route_started", "trip_id": "t"}]))
    assert run(memory.count_turns("d")) == 1 and run(trip_events.for_trip("d", "t"))[0]["kind"] == "route_started"


def test_first_connection_migrates_without_an_explicit_call(sqlite_store):
    run(memory.add("d", [("driver", "hello")]))  # opens the connection: migrations run inside
    con = sqlite3.connect(str(sqlite_store))
    assert con.execute("SELECT count(*) FROM schema_version").fetchone()[0] >= 1
    con.close()


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL (a throwaway Postgres)")
def test_postgres_migrates_and_round_trips(monkeypatch):
    """Run with a throwaway Postgres, e.g.
    docker run -d --name tb-pg -e POSTGRES_PASSWORD=tb -e POSTGRES_DB=truckbuddy_test -p 5433:5432 postgres:16
    TEST_DATABASE_URL=postgresql://postgres:tb@localhost:5433/truckbuddy_test python -m pytest -n 0 tests/offline/test_db_migrations.py"""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("MEMORY_STORE", "pg")

    async def scenario():
        # the pool is bound to one event loop: everything, including close(), happens inside this coroutine
        pool = await db._db()
        await pool.execute("DROP TABLE IF EXISTS conversation, driver_facts, trip_events, schema_version CASCADE")
        await db.close()
        await db.migrate()  # reopening the pool migrates the dropped schema back
        assert await db.migrate() == []
        pool = await db._db()
        versions = [r["version"] for r in await pool.fetch("SELECT version FROM schema_version ORDER BY version")]
        assert versions == [v for v, _ in db.migration_files("pg")]
        await memory.add("pgd", [("driver", "hi"), ("copilot", "Hello.")])
        assert [t["text"] for t in await memory.recent("pgd", 10)] == ["hi", "Hello."]
        await trip_events.add("pgd", [{"kind": "route_started", "trip_id": "t1", "data": {"place": "p"}}])
        assert (await trip_events.for_trip("pgd", "t1"))[0]["data"] == {"place": "p"}
        assert await memory.clear("pgd") == 2 and await trip_events.trips("pgd") == []
        await db.close()

    try:
        run(scenario())
    finally:  # the pool died with its loop; a later test must not find a closed pool
        db._pool, db._pool_lock = None, None
