-- Trip events learn whose fleet and which truck they belong to, and where the truck was: what a fleet
-- dashboard reads ("where are my trucks, why did this one stop").
ALTER TABLE trip_events ADD COLUMN IF NOT EXISTS org_id TEXT;
ALTER TABLE trip_events ADD COLUMN IF NOT EXISTS vehicle_id TEXT;
ALTER TABLE trip_events ADD COLUMN IF NOT EXISTS lat DOUBLE PRECISION;
ALTER TABLE trip_events ADD COLUMN IF NOT EXISTS lng DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS trip_events_org_at ON trip_events (org_id, at);
CREATE INDEX IF NOT EXISTS trip_events_vehicle_at ON trip_events (vehicle_id, at);
-- A fleet's Motive key answers for every driver of the company; this is which Motive user the driver is.
ALTER TABLE drivers ADD COLUMN IF NOT EXISTS motive_user_id TEXT;
