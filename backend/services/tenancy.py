"""Organisations, drivers, vehicles and devices (migration 0004).

A device token's `sub` is still the key everything else hangs on; this module gives it an organisation and
a driver row. A solo driver gets an organisation of one on first contact (`ensure_solo_org`, idempotent,
cached per process). A fleet hands the driver a one-time code that binds the phone to the fleet (and a
truck); the token then carries org_id/driver_id/vehicle_id. The truck profile lives on the vehicle when the
device is bound to one, else on the driver, and is what the agent uses when the app sends the default.

Every function returns None / does nothing when the store is off (keyless dev, most offline tests)."""
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from models import TruckProfile
from services import db

logger = logging.getLogger("truckbuddy.tenancy")

CODE_DAYS = 14  # a fleet code is good for two weeks
PROFILE_CACHE_S = 60.0


@dataclass(frozen=True)
class Device:
    sub: str
    driver_id: str
    org_id: str
    vehicle_id: Optional[str]
    role: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat()


def _at(v) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v))


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


# sub -> (device, read at), per process. A bind drops this worker's entry; the other workers re-read the row
# after DEVICE_TTL_S, so a driver moved to another fleet stops writing events to the old one within a minute.
_devices: dict[str, tuple[Device, float]] = {}
DEVICE_TTL_S = 60.0


async def device_of(sub: str) -> Optional[Device]:
    """The device row for a token's sub, cached; None when the store is off or the device is unknown."""
    hit = _devices.get(sub)
    if hit and time.monotonic() - hit[1] < DEVICE_TTL_S:
        return hit[0]
    k = db.store()
    if k == "off":
        return None
    if k == "sqlite":
        row = await db._sq_run(lambda con: con.execute(
            "SELECT sub, driver_id, org_id, vehicle_id, role FROM devices WHERE sub = ?", (sub,)).fetchone())
    else:
        pool = await db._db()
        r = await pool.fetchrow("SELECT sub, driver_id, org_id, vehicle_id, role FROM devices WHERE sub = $1", sub)
        row = tuple(r) if r else None
    if not row:
        return None
    d = Device(*row)
    _devices[sub] = (d, time.monotonic())
    return d


async def ensure_solo_org(sub: str, role: str = "driver") -> Optional[Device]:
    """The device's org/driver rows, created as an organisation of one when this sub is new."""
    d = await device_of(sub)
    if d is not None:
        return d
    k = db.store()
    if k == "off":
        return None
    org_id, driver_id, now = new_id("org"), new_id("drv"), _now()
    if k == "sqlite":
        def _mk(con):
            con.execute("INSERT INTO orgs (id, name, solo, created_at) VALUES (?, ?, 1, ?)", (org_id, "Solo", _iso(now)))
            con.execute("INSERT INTO drivers (id, org_id, display_name, profile, created_at) VALUES (?, ?, NULL, NULL, ?)",
                        (driver_id, org_id, _iso(now)))
            con.execute("INSERT OR IGNORE INTO devices (sub, driver_id, org_id, vehicle_id, role, bound_at, seen_at) "
                        "VALUES (?, ?, ?, NULL, ?, ?, ?)", (sub, driver_id, org_id, role, _iso(now), _iso(now)))
        await db._sq_run(_mk)
    else:
        pool = await db._db()
        async with pool.acquire() as con:
            async with con.transaction():
                await con.execute("INSERT INTO orgs (id, name, solo, created_at) VALUES ($1, 'Solo', 1, $2)", org_id, now)
                await con.execute("INSERT INTO drivers (id, org_id, created_at) VALUES ($1, $2, $3)", driver_id, org_id, now)
                await con.execute("INSERT INTO devices (sub, driver_id, org_id, role, bound_at, seen_at) VALUES ($1, $2, $3, $4, $5, $5) "
                                  "ON CONFLICT (sub) DO NOTHING", sub, driver_id, org_id, role, now)
    _devices.pop(sub, None)
    return await device_of(sub)


# ---- fleets --------------------------------------------------------------------------------------------------

async def create_org(name: str) -> str:
    org_id, now = new_id("org"), _now()
    k = db.store()
    if k == "off":
        raise RuntimeError("store is off")
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute(
            "INSERT INTO orgs (id, name, solo, created_at) VALUES (?, ?, 0, ?)", (org_id, name, _iso(now))))
    else:
        pool = await db._db()
        await pool.execute("INSERT INTO orgs (id, name, solo, created_at) VALUES ($1, $2, 0, $3)", org_id, name, now)
    return org_id


async def create_vehicle(org_id: str, label: str, profile: Optional[TruckProfile] = None) -> str:
    vid, now = new_id("veh"), _now()
    pj = profile.model_dump_json() if profile else None
    k = db.store()
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute(
            "INSERT INTO vehicles (id, org_id, label, profile, created_at) VALUES (?, ?, ?, ?, ?)", (vid, org_id, label, pj, _iso(now))))
    else:
        pool = await db._db()
        await pool.execute("INSERT INTO vehicles (id, org_id, label, profile, created_at) VALUES ($1, $2, $3, $4::jsonb, $5)",
                           vid, org_id, label, pj, now)
    return vid


async def create_fleet_code(org_id: str, vehicle_id: Optional[str] = None, role: str = "driver",
                            days: int = CODE_DAYS) -> str:
    """A one-time code (8 chars, no confusable letters) a fleet gives a driver to bind the phone."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(8))
    exp = _now() + timedelta(days=days)
    k = db.store()
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute(
            "INSERT INTO fleet_codes (code, org_id, vehicle_id, role, expires_at) VALUES (?, ?, ?, ?, ?)",
            (code, org_id, vehicle_id, role, _iso(exp))))
    else:
        pool = await db._db()
        await pool.execute("INSERT INTO fleet_codes (code, org_id, vehicle_id, role, expires_at) VALUES ($1, $2, $3, $4, $5)",
                           code, org_id, vehicle_id, role, exp)
    return code


class BadFleetCode(ValueError):
    pass


async def bind_fleet_code(sub: str, code: str) -> Device:
    """Binds the device (and its driver row) to the code's org and vehicle; the code is spent. The driver row is
    moved into the fleet, so the driver's memory and trips come along."""
    code = code.strip().upper()
    k = db.store()
    if k == "off":
        raise RuntimeError("store is off")
    now = _now()
    if k == "sqlite":
        row = await db._sq_run(lambda con: con.execute(
            "SELECT org_id, vehicle_id, role, expires_at, used_by FROM fleet_codes WHERE code = ?", (code,)).fetchone())
    else:
        pool = await db._db()
        r = await pool.fetchrow("SELECT org_id, vehicle_id, role, expires_at, used_by FROM fleet_codes WHERE code = $1", code)
        row = tuple(r) if r else None
    if not row or row[4] or _at(row[3]) < now:
        raise BadFleetCode("code unknown, used or expired")
    org_id, vehicle_id, role = row[0], row[1], row[2]
    dev = await ensure_solo_org(sub, role)
    if k == "sqlite":
        def _bind(con):
            con.execute("UPDATE drivers SET org_id = ? WHERE id = ?", (org_id, dev.driver_id))
            con.execute("UPDATE devices SET org_id = ?, vehicle_id = ?, role = ?, bound_at = ? WHERE sub = ?",
                        (org_id, vehicle_id, role, _iso(now), sub))
            con.execute("UPDATE fleet_codes SET used_by = ?, used_at = ? WHERE code = ?", (sub, _iso(now), code))
        await db._sq_run(_bind)
    else:
        pool = await db._db()
        async with pool.acquire() as con:
            async with con.transaction():
                await con.execute("UPDATE drivers SET org_id = $1 WHERE id = $2", org_id, dev.driver_id)
                await con.execute("UPDATE devices SET org_id = $1, vehicle_id = $2, role = $3, bound_at = $4 WHERE sub = $5",
                                  org_id, vehicle_id, role, now, sub)
                await con.execute("UPDATE fleet_codes SET used_by = $1, used_at = $2 WHERE code = $3", sub, now, code)
    _devices.pop(sub, None)
    _profiles.pop(sub, None)
    return await device_of(sub)


async def org_members(org_id: str) -> list[str]:
    """Driver subs (devices) in the org."""
    k = db.store()
    if k == "sqlite":
        rows = await db._sq_run(lambda con: con.execute("SELECT sub FROM devices WHERE org_id = ?", (org_id,)).fetchall())
        return [r[0] for r in rows]
    pool = await db._db()
    return [r["sub"] for r in await pool.fetch("SELECT sub FROM devices WHERE org_id = $1", org_id)]


async def org_vehicles(org_id: str) -> list[dict]:
    k = db.store()
    if k == "sqlite":
        rows = await db._sq_run(lambda con: con.execute(
            "SELECT v.id, v.label, d.sub FROM vehicles v LEFT JOIN devices d ON d.vehicle_id = v.id WHERE v.org_id = ? ORDER BY v.label",
            (org_id,)).fetchall())
    else:
        pool = await db._db()
        rows = [tuple(r) for r in await pool.fetch(
            "SELECT v.id, v.label, d.sub FROM vehicles v LEFT JOIN devices d ON d.vehicle_id = v.id "
            "WHERE v.org_id = $1 ORDER BY v.label", org_id)]
    out: dict[str, dict] = {}
    for vid, label, sub in rows:
        v = out.setdefault(vid, {"id": vid, "label": label, "devices": []})
        if sub:
            v["devices"].append(sub)
    return list(out.values())


async def org_drivers(org_id: str) -> list[dict]:
    """The org's drivers with their truck (when a device is bound to one) and whether Motive knows them."""
    sql = ("SELECT r.id, r.display_name, r.motive_user_id, d.vehicle_id FROM drivers r LEFT JOIN devices d ON d.driver_id = r.id "
           "WHERE r.org_id = {p} ORDER BY r.created_at, r.id")
    k = db.store()
    if k == "sqlite":
        rows = await db._sq_run(lambda con: con.execute(sql.format(p="?"), (org_id,)).fetchall())
    else:
        pool = await db._db()
        rows = [tuple(r) for r in await pool.fetch(sql.format(p="$1"), org_id)]
    out: dict[str, dict] = {}
    for did, name, motive, vid in rows:
        d = out.setdefault(did, {"id": did, "display_name": name, "motive_linked": bool(motive), "vehicle_ids": []})
        if vid and vid not in d["vehicle_ids"]:
            d["vehicle_ids"].append(vid)
    return list(out.values())


class MotiveUserTaken(Exception):
    """Another driver of the org is already this Motive user."""


async def set_motive_user(org_id: str, driver_id: str, user_id: Optional[str]) -> bool:
    """Maps the org's driver to a Motive user id (None unmaps); False when the driver is not in this org,
    MotiveUserTaken when another driver of the org already has that id (their hours would be read twice)."""
    k = db.store()
    if k == "off":
        raise RuntimeError("store is off")
    taken = "SELECT 1 FROM drivers WHERE org_id = {p1} AND motive_user_id = {p2} AND id <> {p3}"
    update = "UPDATE drivers SET motive_user_id = {p2} WHERE org_id = {p1} AND id = {p3}"
    if k == "sqlite":
        def _set(con):  # check and write under the one connection's lock
            if user_id is not None and con.execute(taken.format(p1="?", p2="?", p3="?"), (org_id, user_id, driver_id)).fetchone():
                return None
            return con.execute(update.format(p1="?", p2="?", p3="?"), (user_id, org_id, driver_id)).rowcount
        n = await db._sq_run(_set)
    else:
        pool = await db._db()
        async with pool.acquire() as con, con.transaction():
            await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"motive:{org_id}")
            if user_id is not None and await con.fetchval(taken.format(p1="$1", p2="$2", p3="$3"), org_id, user_id, driver_id):
                n = None
            else:
                status = await con.execute(update.format(p1="$1", p2="$2", p3="$3"), org_id, user_id, driver_id)
                n = int(status.split()[-1])
    if n is None:
        raise MotiveUserTaken(user_id)
    _motive_users.pop(driver_id, None)
    return n > 0


_motive_users: dict[str, tuple[Optional[str], float]] = {}


async def motive_user(driver_id: Optional[str]) -> Optional[str]:
    """The driver's Motive user id (set by the fleet), cached a minute per process."""
    if not driver_id or db.store() == "off":
        return None
    hit = _motive_users.get(driver_id)
    if hit and time.monotonic() - hit[1] < PROFILE_CACHE_S:
        return hit[0]
    k = db.store()
    if k == "sqlite":
        row = await db._sq_run(lambda con: con.execute("SELECT motive_user_id FROM drivers WHERE id = ?", (driver_id,)).fetchone())
        uid = row[0] if row else None
    else:
        pool = await db._db()
        uid = await pool.fetchval("SELECT motive_user_id FROM drivers WHERE id = $1", driver_id)
    _motive_users[driver_id] = (uid, time.monotonic())
    return uid


# ---- refresh tokens ------------------------------------------------------------------------------------------

async def set_refresh(sub: str, refresh_hash: Optional[str]) -> None:
    k = db.store()
    if k == "off":
        return
    now = _now()
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute(
            "UPDATE devices SET refresh_hash = ?, seen_at = ? WHERE sub = ?", (refresh_hash, _iso(now), sub)))
    else:
        pool = await db._db()
        await pool.execute("UPDATE devices SET refresh_hash = $1, seen_at = $2 WHERE sub = $3", refresh_hash, now, sub)


async def sub_by_refresh(refresh_hash: str) -> Optional[str]:
    """The device whose current refresh token hashes to this, or None."""
    k = db.store()
    if k == "off":
        return None
    if k == "sqlite":
        row = await db._sq_run(lambda con: con.execute(
            "SELECT sub FROM devices WHERE refresh_hash = ?", (refresh_hash,)).fetchone())
        return row[0] if row else None
    pool = await db._db()
    return await pool.fetchval("SELECT sub FROM devices WHERE refresh_hash = $1", refresh_hash)


async def refresh_hash_of(sub: str) -> Optional[str]:
    k = db.store()
    if k == "off":
        return None
    if k == "sqlite":
        row = await db._sq_run(lambda con: con.execute(
            "SELECT refresh_hash FROM devices WHERE sub = ?", (sub,)).fetchone())
        return row[0] if row else None
    pool = await db._db()
    return await pool.fetchval("SELECT refresh_hash FROM devices WHERE sub = $1", sub)


# ---- truck profile -------------------------------------------------------------------------------------------

_profiles: dict[str, tuple[Optional[TruckProfile], float]] = {}  # sub -> (profile, monotonic time)


def _profile_target(dev: Device) -> tuple[str, str]:
    return ("vehicles", dev.vehicle_id) if dev.vehicle_id else ("drivers", dev.driver_id)


async def profile_get(sub: str) -> Optional[TruckProfile]:
    """The stored truck profile for this device (the vehicle's when bound to one), or None."""
    hit = _profiles.get(sub)
    if hit and time.monotonic() - hit[1] < PROFILE_CACHE_S:
        return hit[0]
    dev = await device_of(sub)
    if dev is None:
        return None
    table, row_id = _profile_target(dev)
    k = db.store()
    if k == "sqlite":
        row = await db._sq_run(lambda con: con.execute(f"SELECT profile FROM {table} WHERE id = ?", (row_id,)).fetchone())
        text = row[0] if row else None
    else:
        pool = await db._db()
        text = await pool.fetchval(f"SELECT profile::text FROM {table} WHERE id = $1", row_id)
    profile = None
    if text:
        try:
            profile = TruckProfile(**json.loads(text))
        except Exception as e:  # noqa: BLE001 - a bad row must not break every request
            logger.warning("stored profile for %s unreadable: %s", sub, e)
    _profiles[sub] = (profile, time.monotonic())
    return profile


async def profile_put(sub: str, profile: TruckProfile) -> None:
    dev = await ensure_solo_org(sub)
    if dev is None:
        raise RuntimeError("store is off")
    table, row_id = _profile_target(dev)
    text = profile.model_dump_json()
    k = db.store()
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute(f"UPDATE {table} SET profile = ? WHERE id = ?", (text, row_id)))
    else:
        pool = await db._db()
        await pool.execute(f"UPDATE {table} SET profile = $1::jsonb WHERE id = $2", text, row_id)
    _profiles[sub] = (profile, time.monotonic())


async def profile_clear(sub: str) -> bool:
    """"Delete my data": the driver's own stored profile goes; a fleet truck's profile is the fleet's and stays.
    True when there was one."""
    dev = await device_of(sub)
    _profiles.pop(sub, None)
    if dev is None:
        return False
    k = db.store()
    if k == "sqlite":
        n = await db._sq_run(lambda con: con.execute(
            "UPDATE drivers SET profile = NULL WHERE id = ? AND profile IS NOT NULL", (dev.driver_id,)).rowcount)
    else:
        pool = await db._db()
        status = await pool.execute("UPDATE drivers SET profile = NULL WHERE id = $1 AND profile IS NOT NULL", dev.driver_id)
        n = int(status.split()[-1])
    return n > 0


_DEFAULT = TruckProfile()


async def effective_profile(sub: Optional[str], sent: TruckProfile) -> TruckProfile:
    """What the agent should route for: the app's profile when it is anything but the bare default, else the
    stored one (a fleet's vehicle, or what the driver saved through /api/me/profile), else the default."""
    if not sub or sent != _DEFAULT or db.store() == "off":
        return sent
    try:
        stored = await profile_get(sub)
    except Exception as e:  # noqa: BLE001
        logger.info("stored profile lookup skipped: %s", e)
        return sent
    return stored or sent


# ---- org credentials -----------------------------------------------------------------------------------------

async def credential_put(org_id: str, provider: str, secret: str) -> None:
    from services import secrets as sec
    enc, now = sec.encrypt(secret), _now()
    k = db.store()
    if k == "off":
        raise RuntimeError("store is off")
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute(
            "INSERT INTO org_credentials (org_id, provider, secret_enc, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(org_id, provider) DO UPDATE SET secret_enc = excluded.secret_enc, updated_at = excluded.updated_at",
            (org_id, provider, enc, _iso(now))))
    else:
        pool = await db._db()
        await pool.execute(
            "INSERT INTO org_credentials (org_id, provider, secret_enc, updated_at) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (org_id, provider) DO UPDATE SET secret_enc = EXCLUDED.secret_enc, updated_at = EXCLUDED.updated_at",
            org_id, provider, enc, now)
    _credentials.pop((org_id, provider), None)


_credentials: dict[tuple[str, str], tuple[Optional[str], float]] = {}


async def credential_get(org_id: Optional[str], provider: str) -> Optional[str]:
    """The decrypted secret, cached a minute per process; None without one (or without CREDENTIALS_KEY)."""
    if not org_id or db.store() == "off":
        return None
    hit = _credentials.get((org_id, provider))
    if hit and time.monotonic() - hit[1] < PROFILE_CACHE_S:
        return hit[0]
    k = db.store()
    if k == "sqlite":
        row = await db._sq_run(lambda con: con.execute(
            "SELECT secret_enc FROM org_credentials WHERE org_id = ? AND provider = ?", (org_id, provider)).fetchone())
        enc = row[0] if row else None
    else:
        pool = await db._db()
        enc = await pool.fetchval("SELECT secret_enc FROM org_credentials WHERE org_id = $1 AND provider = $2", org_id, provider)
    secret = None
    if enc:
        from services import secrets as sec
        try:
            secret = sec.decrypt(enc)
        except sec.NoCredentialsKey as e:
            logger.warning("org credential %s/%s unreadable: %s", org_id, provider, e)
    _credentials[(org_id, provider)] = (secret, time.monotonic())
    return secret


def reset_caches() -> None:
    """Tests, and after a bind: forget the per-process copies."""
    _devices.clear()
    _profiles.clear()
    _credentials.clear()
    _motive_users.clear()
