-- Sign in with Apple / Google (services/accounts.py). An account's `sub` is the same key every other table already
-- hangs a driver's data on, so signing in on another phone finds the data without moving a row; the device, driver
-- and solo-org rows for it are made by tenancy.ensure_solo_org as for any sub. One account_sessions row per signed-in
-- phone, each with its own refresh token; a token carries the session id (`sid`) and stops working when the row goes.
-- prev_refresh_hash / rotated_at: the refresh token before the current one and when it was replaced, so a copy of
-- a spent token showing up later is recognised as theft and closes the session (services/auth.py refresh).
-- apple_refresh_enc: Apple's refresh token, encrypted (services/secrets.py), kept so account deletion can revoke it.
-- 0014: #149 takes 0011.
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    sub TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    provider_sub TEXT NOT NULL,
    email TEXT,
    name TEXT,
    apple_refresh_enc TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (provider, provider_sub)
);
CREATE TABLE IF NOT EXISTS account_sessions (
    id TEXT PRIMARY KEY,
    sub TEXT NOT NULL,
    refresh_hash TEXT NOT NULL UNIQUE,
    prev_refresh_hash TEXT,
    rotated_at TEXT,
    platform TEXT,
    created_at TEXT NOT NULL,
    seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS account_sessions_sub ON account_sessions (sub);
CREATE INDEX IF NOT EXISTS account_sessions_prev ON account_sessions (prev_refresh_hash);
