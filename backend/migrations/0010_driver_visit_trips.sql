-- A visit counts once per place per trip (review 2026-11-02): 0009 kept only the last trip per place, so a batch
-- the app sent again with two trips at the same place counted both again. Every counted trip gets a row, pruned
-- with the place's visits; the trip 0009 kept is carried over, and its column goes.
CREATE TABLE IF NOT EXISTS driver_visit_trips (
    driver TEXT NOT NULL,
    place_id TEXT NOT NULL,
    trip_id TEXT NOT NULL,
    PRIMARY KEY (driver, place_id, trip_id)
);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema()
               AND table_name = 'driver_visits' AND column_name = 'last_trip') THEN
        INSERT INTO driver_visit_trips (driver, place_id, trip_id)
            SELECT driver, place_id, last_trip FROM driver_visits WHERE last_trip IS NOT NULL
            ON CONFLICT DO NOTHING;
        ALTER TABLE driver_visits DROP COLUMN last_trip;
    END IF;
END $$;
