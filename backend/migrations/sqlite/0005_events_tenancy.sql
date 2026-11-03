ALTER TABLE trip_events ADD COLUMN org_id TEXT;
ALTER TABLE trip_events ADD COLUMN vehicle_id TEXT;
ALTER TABLE trip_events ADD COLUMN lat REAL;
ALTER TABLE trip_events ADD COLUMN lng REAL;
CREATE INDEX IF NOT EXISTS trip_events_org_at ON trip_events (org_id, at);
CREATE INDEX IF NOT EXISTS trip_events_vehicle_at ON trip_events (vehicle_id, at);
ALTER TABLE drivers ADD COLUMN motive_user_id TEXT;
