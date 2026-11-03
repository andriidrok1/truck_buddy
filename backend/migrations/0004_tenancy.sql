-- Who the driver is beyond a device token: an organisation (a fleet, or one of one for a solo driver), its
-- people (owner, dispatcher), its trucks, its drivers and the devices bound to them, the one-time codes that
-- bind a phone to a fleet, and the fleet's own provider credentials (Motive) encrypted at rest.
CREATE TABLE IF NOT EXISTS orgs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    solo INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(id),
    email TEXT,
    role TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS users_org ON users (org_id);
CREATE TABLE IF NOT EXISTS vehicles (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(id),
    label TEXT NOT NULL,
    profile JSONB,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS vehicles_org ON vehicles (org_id);
CREATE TABLE IF NOT EXISTS drivers (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(id),
    display_name TEXT,
    profile JSONB,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS drivers_org ON drivers (org_id);
CREATE TABLE IF NOT EXISTS devices (
    sub TEXT PRIMARY KEY,
    driver_id TEXT NOT NULL REFERENCES drivers(id),
    org_id TEXT NOT NULL REFERENCES orgs(id),
    vehicle_id TEXT REFERENCES vehicles(id),
    role TEXT NOT NULL DEFAULT 'driver',
    refresh_hash TEXT,
    bound_at TIMESTAMPTZ NOT NULL,
    seen_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS devices_org ON devices (org_id);
CREATE TABLE IF NOT EXISTS fleet_codes (
    code TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(id),
    vehicle_id TEXT REFERENCES vehicles(id),
    role TEXT NOT NULL DEFAULT 'driver',
    expires_at TIMESTAMPTZ NOT NULL,
    used_by TEXT,
    used_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS org_credentials (
    org_id TEXT NOT NULL REFERENCES orgs(id),
    provider TEXT NOT NULL,
    secret_enc TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (org_id, provider)
);
