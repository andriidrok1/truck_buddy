CREATE UNIQUE INDEX IF NOT EXISTS drivers_org_motive_user ON drivers (org_id, motive_user_id) WHERE motive_user_id IS NOT NULL;
