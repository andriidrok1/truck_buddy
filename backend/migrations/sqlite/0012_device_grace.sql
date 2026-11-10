-- An expired driver token is honoured for a grace window while the device row exists (services/auth.py _grace).
-- "Delete my data" sets grace_not_before: tokens issued before it get no grace once they expire.
ALTER TABLE devices ADD COLUMN grace_not_before TEXT;
