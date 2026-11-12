-- Sign in with Apple / Google (services/accounts.py). An account's `sub` is the same key every other table already
-- hangs a driver's data on, so signing in on another phone finds the data without moving a row; the device, driver
-- and solo-org rows for it are made by tenancy.ensure_solo_org as for any sub. One account_sessions row per signed-in
-- phone, each with its own refresh token; a token carries the session id (`sid`) and stops working when the row goes.
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
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (provider, provider_sub)
);
CREATE TABLE IF NOT EXISTS account_sessions (
    id TEXT PRIMARY KEY,
    sub TEXT NOT NULL,
    refresh_hash TEXT NOT NULL UNIQUE,
    platform TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    seen_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS account_sessions_sub ON account_sessions (sub);
