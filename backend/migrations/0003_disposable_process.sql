-- State that used to live only in the worker's memory, so a second worker or a restart sees it:
-- breakdown research results, satellite parking verdicts (with the approach to the entrance), and the
-- feeds one worker polls for all (NWS alerts, Caltrans chain controls, TPIMS parking counts).
CREATE TABLE IF NOT EXISTS jobs (
    rid TEXT PRIMARY KEY,
    driver TEXT,
    kind TEXT NOT NULL,
    state JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_updated ON jobs (updated_at);
CREATE TABLE IF NOT EXISTS parking_checks (
    key TEXT PRIMARY KEY,
    check_json JSONB NOT NULL,
    retried INTEGER NOT NULL DEFAULT 0,
    approach JSONB,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_cache (
    name TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL
);
