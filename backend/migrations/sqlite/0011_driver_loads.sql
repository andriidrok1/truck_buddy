-- The owner-operator's own ledger (services/ledger.py): what each load pays, with the route's miles, miles per
-- state, diesel and tolls at the time it was saved. Keyed by the driver's sub only, with no org_id on purpose: a
-- fleet the driver belongs to never reads it (the fleet routes query trip_events by org). Kept until the driver
-- deletes their data (memory.clear), not pruned like the trip events. state_miles is JSON text ({"CA": 82.1}).
CREATE TABLE IF NOT EXISTS driver_loads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    driver TEXT NOT NULL,
    trip_id TEXT,
    created_at TEXT NOT NULL,
    rate_usd REAL NOT NULL,
    dest_name TEXT,
    dest_place_id TEXT,
    miles REAL,
    state_miles TEXT,
    fuel_usd REAL,
    tolls_usd REAL,
    delivered_at TEXT
);
CREATE INDEX IF NOT EXISTS driver_loads_driver_created ON driver_loads (driver, created_at);
CREATE INDEX IF NOT EXISTS driver_loads_driver_trip ON driver_loads (driver, trip_id);
