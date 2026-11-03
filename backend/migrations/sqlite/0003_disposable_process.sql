CREATE TABLE IF NOT EXISTS jobs (
    rid TEXT PRIMARY KEY,
    driver TEXT,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_updated ON jobs (updated_at);
CREATE TABLE IF NOT EXISTS parking_checks (
    key TEXT PRIMARY KEY,
    check_json TEXT NOT NULL,
    retried INTEGER NOT NULL DEFAULT 0,
    approach TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_cache (
    name TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
