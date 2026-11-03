-- Baseline: the tables the backend created with CREATE IF NOT EXISTS before migrations existed
-- (services/memory.py and services/trip_events.py). Postgres dialect; tests use migrations/sqlite/.
CREATE TABLE IF NOT EXISTS conversation (
    id BIGSERIAL PRIMARY KEY,
    driver TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS conversation_driver_at ON conversation (driver, at);

CREATE TABLE IF NOT EXISTS driver_facts (
    driver TEXT PRIMARY KEY,
    facts TEXT NOT NULL,
    turns_seen INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS trip_events (
    id BIGSERIAL PRIMARY KEY,
    driver TEXT NOT NULL,
    trip_id TEXT,
    kind TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS trip_events_driver_at ON trip_events (driver, at);
CREATE INDEX IF NOT EXISTS trip_events_trip_at ON trip_events (trip_id, at);
CREATE INDEX IF NOT EXISTS trip_events_at ON trip_events (at);
