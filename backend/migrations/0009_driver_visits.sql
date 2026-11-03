-- Visits: how many times the driver arrived at a place (the app's "arrived" trip events), one row per place,
-- the 30 most recent kept. Apart from driver_facts: the facts are rewritten whole from a dialog's copy, which
-- would lose a count that came in meanwhile; a row per place is counted up in place.
CREATE TABLE IF NOT EXISTS driver_visits (
    driver TEXT NOT NULL,
    place_id TEXT NOT NULL,
    name TEXT,
    category TEXT,
    count INTEGER NOT NULL DEFAULT 0,
    last_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (driver, place_id)
);
CREATE INDEX IF NOT EXISTS driver_visits_driver_last ON driver_visits (driver, last_at);
