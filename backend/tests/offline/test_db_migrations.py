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


def _versions(con) -> list[int]:
    return [r[0] for r in con.execute("SELECT version FROM schema_version ORDER BY version")]


def test_sqlite_migrations_run_twice_and_survive_a_lost_version_row(tmp_path):
    """0005 is the first ALTER TABLE: a re-run, or a run that died after the columns but before the version row,
    must not fail on "duplicate column name"."""
    con = sqlite3.connect(str(tmp_path / "twice.sqlite"), isolation_level=None)
    every = [v for v, _ in db.migration_files("sqlite")]
    assert db._migrate_sqlite(con) == every and db._migrate_sqlite(con) == []
    con.execute("DELETE FROM schema_version WHERE version = 5")  # the columns stay, the record of them is gone
    assert db._migrate_sqlite(con) == [5] and _versions(con) == every
    con.close()


def test_sqlite_migration_is_atomic_with_its_version_row(tmp_path, monkeypatch):
    """A file that fails halfway leaves neither its first statements nor its version row behind."""
    bad = tmp_path / "0099_bad.sql"
    bad.write_text("CREATE TABLE half_done (x INTEGER);\nTHIS IS NOT SQL;\n")
    files = db.migration_files("sqlite")
    monkeypatch.setattr(db, "migration_files", lambda dialect: [*files, (99, bad)])
    con = sqlite3.connect(str(tmp_path / "atomic.sqlite"), isolation_level=None)
    with pytest.raises(sqlite3.OperationalError):
        db._migrate_sqlite(con)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "half_done" not in tables and 99 not in _versions(con) and 4 in _versions(con)
    con.close()


def test_sqlite_at_0004_with_rows_upgrades(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite"
    files = db.migration_files("sqlite")
    monkeypatch.setattr(db, "migration_files", lambda dialect: [f for f in files if f[0] <= 4])
    con = sqlite3.connect(str(path), isolation_level=None)
    assert db._migrate_sqlite(con) == [1, 2, 3, 4]
    con.execute("INSERT INTO orgs (id, name, solo, created_at) VALUES ('o1', 'Acme', 0, '2026-10-01T00:00:00+00:00')")
    con.execute("INSERT INTO drivers (id, org_id, display_name, profile, created_at) "
                "VALUES ('dr1', 'o1', 'Sam', NULL, '2026-10-01T00:00:00+00:00')")
    con.execute("INSERT INTO trip_events (driver, trip_id, kind, at, source, data) "
                "VALUES ('d1', 't1', 'route_started', '2026-10-01T17:00:00+00:00', 'app', '{}')")
    monkeypatch.setattr(db, "migration_files", lambda dialect: files)
    assert db._migrate_sqlite(con) == [v for v, _ in files if v > 4]
    row = con.execute("SELECT driver, kind, org_id, vehicle_id, lat, lng FROM trip_events").fetchone()
    assert row == ("d1", "route_started", None, None, None, None)
    assert con.execute("SELECT id, display_name, motive_user_id FROM drivers").fetchone() == ("dr1", "Sam", None)
    assert _versions(con) == [v for v, _ in files]
    con.close()


def test_sqlite_at_0009_keeps_the_trips_already_counted(tmp_path, monkeypatch):
    """0010 moves the one trip 0009 kept per place (last_trip) into driver_visit_trips: that trip sent again after
    the upgrade is not another visit."""
    path = tmp_path / "visits.sqlite"
    files = db.migration_files("sqlite")
    monkeypatch.setattr(db, "migration_files", lambda dialect: [f for f in files if f[0] <= 9])
    con = sqlite3.connect(str(path), isolation_level=None)
    db._migrate_sqlite(con)
    con.execute("INSERT INTO driver_visits (driver, place_id, name, category, count, last_at, last_trip) "
                "VALUES ('d1', 'g-pilot', 'Pilot', 'truck_stop', 1, '2026-11-01T17:00:00+00:00', 't1')")
    monkeypatch.setattr(db, "migration_files", lambda dialect: files)
    assert 10 in db._migrate_sqlite(con)
    assert con.execute("SELECT driver, place_id, trip_id FROM driver_visit_trips").fetchall() == [("d1", "g-pilot", "t1")]
    assert "last_trip" not in {r[1] for r in con.execute("PRAGMA table_info(driver_visits)")}
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
