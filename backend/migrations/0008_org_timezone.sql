-- A fleet's "today" is its own calendar day: UTC split a California evening shift across two days (bug 4).
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles';
