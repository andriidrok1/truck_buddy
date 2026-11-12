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
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg

from models import TruckProfile
from services import db
from services.db import iso as _iso, parse_at as _at, utcnow as _now

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


# Deletes a solo org that nothing points at any more (`{p}` = its id): its driver left for a fleet or deleted the
# account. Never a fleet (solo = 0).
DROP_EMPTY_SOLO_ORG = "DELETE FROM orgs WHERE id = {p} AND solo = 1 AND " + " AND ".join(
    f"NOT EXISTS (SELECT 1 FROM {t} WHERE org_id = orgs.id)"
    for t in ("devices", "drivers", "vehicles", "fleet_codes", "org_credentials", "users"))

_created: dict[str, Optional[datetime]] = {}  # sub -> its driver row's created_at (never changes), per process


async def device_created(sub: Optional[str]) -> Optional[datetime]:
    """When this phone first got a driver row: devices has no created_at of its own (bound_at moves on a fleet
    bind), and the driver row is made with the device and moves with it into a fleet. None when the store is off,
    the device is unknown or the lookup fails (the caller then treats the device as not new)."""
    if not sub:
        return None
    if sub in _created:
        return _created[sub]
    k = db.store()
    if k == "off":
        return None
    try:
        sql = "SELECT r.created_at FROM devices d JOIN drivers r ON r.id = d.driver_id WHERE d.sub = {p}"
        if k == "sqlite":
            row = await db._sq_run(lambda con: con.execute(sql.format(p="?"), (sub,)).fetchone())
            at = _at(row[0]) if row else None
        else:
            pool = await db._db()
            v = await pool.fetchval(sql.format(p="$1"), sub)
            at = _at(v) if v is not None else None
    except Exception as e:  # noqa: BLE001 - a store hiccup must not cost the driver a turn
        logger.warning("device age lookup failed for %s: %s", sub, type(e).__name__)
        return None
    if at is not None:
        if len(_created) > 50_000:
            _created.clear()
        _created[sub] = at
    return at


class Unregistered(LookupError):
    """The sub has no device row and the caller may not make one (an account's rows are made at sign-in only)."""


def solo_org_statements(sub: str, role: str, at) -> list[tuple[str, tuple]]:
    """The inserts that give a new sub its organisation of one, driver row and device row (`?` placeholders; `at`
    is the timestamp in the store's own form). For a caller that runs them inside its own transaction: signing an
    account in (services/accounts.py)."""
    org_id, driver_id = new_id("org"), new_id("drv")
    return [("INSERT INTO orgs (id, name, solo, created_at) VALUES (?, 'Solo', 1, ?)", (org_id, at)),
            ("INSERT INTO drivers (id, org_id, created_at) VALUES (?, ?, ?)", (driver_id, org_id, at)),
            ("INSERT INTO devices (sub, driver_id, org_id, role, bound_at, seen_at) VALUES (?, ?, ?, ?, ?, ?)",
             (sub, driver_id, org_id, role, at, at))]


async def ensure_solo_org(sub: str, role: str = "driver") -> Optional[Device]:
    """The device's org/driver rows, created as an organisation of one when this sub is new.

    Only for a device or access-code token's sub, which exists by being used. Never for an account's: once the
    account is deleted its sub looks like a new one, and making rows for it here would bring a deleted driver back
    (a request or a refresh that was on its way during the deletion). An account's rows are made at sign-in, in
    the transaction that checks the account is still there (accounts.session_open); everything else reads them
    with device_of."""
    d = await device_of(sub)
    if d is not None:
        return d
    k = db.store()
    if k == "off":
        return None
    org_id, driver_id, now = new_id("org"), new_id("drv"), _now()
    # Two first requests for one sub (a phone waking two screens, two sign-ins at once) both get here; one device
    # row wins. The loser takes its org and driver rows back: nothing points at them, and no sub is on them to find
    # them by when the driver deletes the account.
    lost_driver, lost_org = "DELETE FROM drivers WHERE id = {p}", "DELETE FROM orgs WHERE id = {p}"
    if k == "sqlite":
        def _mk(con):
            con.execute("INSERT INTO orgs (id, name, solo, created_at) VALUES (?, ?, 1, ?)", (org_id, "Solo", _iso(now)))
            con.execute("INSERT INTO drivers (id, org_id, display_name, profile, created_at) VALUES (?, ?, NULL, NULL, ?)",
                        (driver_id, org_id, _iso(now)))
            n = con.execute("INSERT OR IGNORE INTO devices (sub, driver_id, org_id, vehicle_id, role, bound_at, seen_at) "
                            "VALUES (?, ?, ?, NULL, ?, ?, ?)", (sub, driver_id, org_id, role, _iso(now), _iso(now))).rowcount
            if not n:
                con.execute(lost_driver.format(p="?"), (driver_id,))
                con.execute(lost_org.format(p="?"), (org_id,))
        await db._sq_run(_mk)
    else:
        pool = await db._db()
        async with pool.acquire() as con:
            async with con.transaction():
                await con.execute("INSERT INTO orgs (id, name, solo, created_at) VALUES ($1, 'Solo', 1, $2)", org_id, now)
                await con.execute("INSERT INTO drivers (id, org_id, created_at) VALUES ($1, $2, $3)", driver_id, org_id, now)
                status = await con.execute(
                    "INSERT INTO devices (sub, driver_id, org_id, role, bound_at, seen_at) VALUES ($1, $2, $3, $4, $5, $5) "
                    "ON CONFLICT (sub) DO NOTHING", sub, driver_id, org_id, role, now)
                if status.split()[-1] == "0":
                    await con.execute(lost_driver.format(p="$1"), driver_id)
                    await con.execute(lost_org.format(p="$1"), org_id)
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


# Row locks that transactions touching an account or a fleet take first, in this order, so two of them never wait for
# each other in a circle (Postgres; SQLite has one writer at a time and drops FOR UPDATE, services/db.py run_script).
ACCOUNT_LOCK = "SELECT 1 FROM accounts WHERE sub = ? FOR UPDATE"  # sign-in, account deletion, an account's bind
ORG_LOCK = "SELECT id FROM orgs WHERE id = ? FOR UPDATE"  # a fleet's last owner leaving, anybody joining it


def _bind_script(sub: str, code: str, account: bool, now: datetime, at):
    """Binds in one transaction. Everything is read again inside it, under the locks a deletion takes too: the
    account (for an account's sub), the code (still unused, not expired), the fleet and the truck (not closed by
    their owner meanwhile) and the device. A step that finds its row gone rolls the whole bind back, so a code is
    never spent by a phone that is not bound by it."""
    if account and not (yield (ACCOUNT_LOCK, (sub,))):
        raise Unregistered(sub)  # deleted while this request was on its way
    rows = yield ("SELECT org_id, vehicle_id, role, expires_at, used_by FROM fleet_codes WHERE code = ?", (code,))
    if not rows or rows[0][4] or _at(rows[0][3]) < now:
        raise BadFleetCode("code unknown, used or expired")
    org_id, vehicle_id, role = rows[0][:3]
    if not (yield (ORG_LOCK, (org_id,))):
        raise BadFleetCode("the fleet is gone")
    if vehicle_id and not (yield ("SELECT 1 FROM vehicles WHERE id = ? AND org_id = ?", (vehicle_id, org_id))):
        raise BadFleetCode("the truck is gone")
    dev = yield ("SELECT driver_id, org_id FROM devices WHERE sub = ? FOR UPDATE", (sub,))
    if not dev:
        raise Unregistered(sub)
    driver_id, old_org = dev[0]
    yield ("UPDATE drivers SET org_id = ? WHERE id = ?", (org_id, driver_id))
    moved = yield ("UPDATE devices SET org_id = ?, vehicle_id = ?, role = ?, bound_at = ? WHERE sub = ? RETURNING sub",
                   (org_id, vehicle_id, role, at, sub))
    if len(moved) != 1:
        raise Unregistered(sub)
    if not (yield ("UPDATE fleet_codes SET used_by = ?, used_at = ? WHERE code = ? AND used_by IS NULL RETURNING code",
                   (sub, at, code))):
        raise BadFleetCode("code used meanwhile")  # two phones with one code: the first one has it
    yield (DROP_EMPTY_SOLO_ORG.format(p="?"), (old_org,))  # the org of one the driver came from


async def bind_fleet_code(sub: str, code: str, create: bool = True) -> Device:
    """Binds the device (and its driver row) to the code's org and vehicle; the code is spent. The driver row is
    moved into the fleet, so the driver's memory and trips come along. `create=False` for an account's sub: its
    rows are not made here (see ensure_solo_org), and the bind holds the account row against a deletion.
    BadFleetCode for a code that is unknown, spent or expired, or whose fleet or truck is gone; Unregistered when
    the sub has no device row (an account deleted meanwhile)."""
    code = code.strip().upper()
    k = db.store()
    if k == "off":
        raise RuntimeError("store is off")
    if create:
        await ensure_solo_org(sub)  # a device token's first contact; the bind itself checks the row again
    now = _now()
    await db.run_script(_bind_script(sub, code, not create, now, _iso(now) if k == "sqlite" else now))
    _devices.pop(sub, None)
    _profiles.pop(sub, None)
    dev = await device_of(sub)
    if dev is None:  # deleted right after the bind committed
        raise Unregistered(sub)
    return dev


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
    """The org's drivers with their truck (when a device is bound to one) and whether Motive knows them.
    A row whose devices are all owner or dispatcher phones is not a driver: binding by code gives everyone a
    drivers row (it carries their memory), and the owner and the dispatcher were listed as drivers (QA
    2026-11-02, bug 3). A row with no device left (a former driver) stays listed."""
    sql = ("SELECT r.id, r.display_name, r.motive_user_id, d.vehicle_id, d.role FROM drivers r "
           "LEFT JOIN devices d ON d.driver_id = r.id WHERE r.org_id = {p} ORDER BY r.created_at, r.id")
    k = db.store()
    if k == "sqlite":
        rows = await db._sq_run(lambda con: con.execute(sql.format(p="?"), (org_id,)).fetchall())
    else:
        pool = await db._db()
        rows = [tuple(r) for r in await pool.fetch(sql.format(p="$1"), org_id)]
    out: dict[str, dict] = {}
    staff: set[str] = set()  # rows seen only with owner/dispatcher devices so far
    for did, name, motive, vid, role in rows:
        new = did not in out
        d = out.setdefault(did, {"id": did, "display_name": name, "motive_linked": bool(motive), "vehicle_ids": []})
        if role is not None and role != "driver":
            if new:
                staff.add(did)
            continue
        staff.discard(did)
        if vid and vid not in d["vehicle_ids"]:
            d["vehicle_ids"].append(vid)
    return [d for did, d in out.items() if did not in staff]


class MotiveUserTaken(Exception):
    """Another driver of the org is already this Motive user."""


async def set_motive_user(org_id: str, driver_id: str, user_id: Optional[str]) -> bool:
    """Maps the org's driver to a Motive user id (None unmaps); False when the driver is not in this org,
    MotiveUserTaken when another driver of the org already has that id (their hours would be read twice). The
    unique index drivers_org_motive_user (0006) decides; a check in code under a lock did before (bug 9)."""
    k = db.store()
    if k == "off":
        raise RuntimeError("store is off")
    update = "UPDATE drivers SET motive_user_id = {p2} WHERE org_id = {p1} AND id = {p3}"
    n: Optional[int]
    if k == "sqlite":
        def _set(con):
            try:
                return con.execute(update.format(p1="?", p2="?", p3="?"), (user_id, org_id, driver_id)).rowcount
            except sqlite3.IntegrityError:
                return None
        n = await db._sq_run(_set)
    else:
        pool = await db._db()
        try:
            status = await pool.execute(update.format(p1="$1", p2="$2", p3="$3"), org_id, user_id, driver_id)
            n = int(status.split()[-1])
        except asyncpg.UniqueViolationError:
            n = None
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


# ---- expired-token grace (services/auth.py _grace) ----------------------------------------------------------

_grace: dict[str, tuple[Optional[tuple[str, Optional[object]]], float]] = {}  # sub -> ((role, not_before) | None, read at)
_touched: dict[str, float] = {}  # sub -> when this worker last wrote seen_at for a grace request
TOUCH_EVERY_S = 3600.0


async def grace_info(sub: str) -> Optional[tuple[str, Optional[object]]]:
    """(role, grace_not_before) of the device row, or None when the store does not know the sub. Cached like
    device_of, so an expired token costs one small read per worker per DEVICE_TTL_S."""
    hit = _grace.get(sub)
    if hit and time.monotonic() - hit[1] < DEVICE_TTL_S:
        return hit[0]
    k = db.store()
    if k == "off":
        return None
    if k == "sqlite":
        row = await db._sq_run(lambda con: con.execute(
            "SELECT role, grace_not_before FROM devices WHERE sub = ?", (sub,)).fetchone())
    else:
        pool = await db._db()
        r = await pool.fetchrow("SELECT role, grace_not_before FROM devices WHERE sub = $1", sub)
        row = tuple(r) if r else None
    info = None if not row else (row[0], _at(row[1]) if row[1] is not None else None)
    if len(_grace) > 50_000:
        _grace.clear()
    _grace[sub] = (info, time.monotonic())
    return info


async def touch(sub: str) -> None:
    """seen_at = now for a device on an expired token (at most once an hour per worker)."""
    now_m = time.monotonic()
    last = _touched.get(sub)
    if last is not None and now_m - last < TOUCH_EVERY_S:
        return
    if len(_touched) > 50_000:
        _touched.clear()
    _touched[sub] = now_m
    k = db.store()
    if k == "off":
        return
    now = _now()
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute("UPDATE devices SET seen_at = ? WHERE sub = ?", (_iso(now), sub)))
    else:
        pool = await db._db()
        await pool.execute("UPDATE devices SET seen_at = $1 WHERE sub = $2", now, sub)


async def revoke_grace(sub: str) -> None:
    """Tokens issued before now get no grace once they expire ("Delete my data"). Tokens still inside their 30 days
    keep working, and a refresh hands out new ones, so the app in use is not signed out."""
    k = db.store()
    if k == "off":
        return
    now = _now().replace(microsecond=0)  # iat is whole seconds: a token minted in this second still counts
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute(
            "UPDATE devices SET grace_not_before = ? WHERE sub = ?", (_iso(now), sub)))
    else:
        pool = await db._db()
        await pool.execute("UPDATE devices SET grace_not_before = $1 WHERE sub = $2", now, sub)
    _grace.pop(sub, None)


# ---- truck profile -------------------------------------------------------------------------------------------

_profiles: dict[str, tuple[Optional[TruckProfile], float]] = {}  # sub -> (profile, monotonic time)


def _profile_target(dev: Device) -> tuple[str, str]:
    return ("vehicles", dev.vehicle_id) if dev.vehicle_id else ("drivers", dev.driver_id)


def _drives_a_fleet_truck(dev: Device) -> bool:
    """A fleet driver on a fleet truck: the truck's sizes are the owner's and the dispatcher's, not theirs."""
    return bool(dev.vehicle_id) and (dev.role or "driver") == "driver"


async def profile_get(sub: str) -> Optional[TruckProfile]:
    """The stored truck profile for this device (the vehicle's when bound to one, with the driver's own name from
    the drivers row), or None."""
    hit = _profiles.get(sub)
    if hit and time.monotonic() - hit[1] < PROFILE_CACHE_S:
        return hit[0]
    dev = await device_of(sub)
    if dev is None:
        return None
    table, row_id = _profile_target(dev)
    k = db.store()
    if k == "sqlite":
        def _read(con):
            row = con.execute(f"SELECT profile FROM {table} WHERE id = ?", (row_id,)).fetchone()
            name = con.execute("SELECT display_name FROM drivers WHERE id = ?", (dev.driver_id,)).fetchone()
            return (row[0] if row else None), (name[0] if name else None)
        text, name = await db._sq_run(_read)
    else:
        pool = await db._db()
        text = await pool.fetchval(f"SELECT profile::text FROM {table} WHERE id = $1", row_id)
        name = await pool.fetchval("SELECT display_name FROM drivers WHERE id = $1", dev.driver_id)
    profile = None
    if text:
        try:
            profile = TruckProfile(**json.loads(text))
        except Exception as e:  # noqa: BLE001 - a bad row must not break every request
            logger.warning("stored profile for %s unreadable: %s", sub, e)
    if profile is not None and table == "vehicles":
        profile = profile.model_copy(update={"driver_name": name})  # a truck has no driver's name of its own
    _profiles[sub] = (profile, time.monotonic())
    return profile


async def has_profile(sub: str) -> bool:
    """Whether this driver has finished setup, which is what the app skips onboarding on: a truck profile of their
    own is stored, or the device is bound to a fleet truck (its sizes are the fleet's; profile_put stores only the
    driver's name there). A stored name alone is not setup: GET /api/me/profile answers 404 for it, and the app
    greeted such a driver with "Welcome back" over a default truck. Read from the store and not from this worker's
    copies: a sign-in right after the profile was saved through another worker must not send the driver to
    onboarding again."""
    _devices.pop(sub, None)
    dev = await device_of(sub)
    if dev is None:
        return False
    if dev.vehicle_id:
        return True
    sql = "SELECT profile IS NOT NULL FROM drivers WHERE id = {p}"
    if db.store() == "sqlite":
        row = await db._sq_run(lambda con: con.execute(sql.format(p="?"), (dev.driver_id,)).fetchone())
        return bool(row and row[0])
    pool = await db._db()
    return bool(await pool.fetchval(sql.format(p="$1"), dev.driver_id))


async def profile_put(sub: str, profile: TruckProfile, create: bool = True) -> None:
    """The app's profile. A fleet driver's sizes are not written anywhere: they change the trip, which the phone
    carries (decision 2026-11-02, bug 2: one driver's trailer and name were written onto the truck for all of
    its drivers). Their name goes to their own drivers row. `create=False` for an account's sub: its rows are not
    made here (see ensure_solo_org), Unregistered when they are gone."""
    k = db.store()
    if k == "off":
        raise RuntimeError("store is off")
    dev = await (ensure_solo_org(sub) if create else device_of(sub))
    if dev is None:
        raise Unregistered(sub)
    if _drives_a_fleet_truck(dev):
        name = profile.driver_name
        if k == "sqlite":
            await db._sq_run(lambda con: con.execute("UPDATE drivers SET display_name = ? WHERE id = ?", (name, dev.driver_id)))
        else:
            pool = await db._db()
            await pool.execute("UPDATE drivers SET display_name = $1 WHERE id = $2", name, dev.driver_id)
        _profiles.pop(sub, None)
        return
    table, row_id = _profile_target(dev)
    text = profile.model_dump_json()
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute(f"UPDATE {table} SET profile = ? WHERE id = ?", (text, row_id)))
    else:
        pool = await db._db()
        await pool.execute(f"UPDATE {table} SET profile = $1::jsonb WHERE id = $2", text, row_id)
    if table == "vehicles":
        _profiles.clear()  # every device on this truck
    else:
        _profiles[sub] = (profile, time.monotonic())


async def vehicle_profile_put(org_id: str, vehicle_id: str, profile: TruckProfile) -> bool:
    """The truck's sizes, set by its fleet's owner or dispatcher; no driver's name. False for a truck that is not
    the org's."""
    text = profile.model_copy(update={"driver_name": None}).model_dump_json()
    k = db.store()
    if k == "off":
        raise RuntimeError("store is off")
    if k == "sqlite":
        n = await db._sq_run(lambda con: con.execute(
            "UPDATE vehicles SET profile = ? WHERE id = ? AND org_id = ?", (text, vehicle_id, org_id)).rowcount)
    else:
        pool = await db._db()
        status = await pool.execute("UPDATE vehicles SET profile = $1::jsonb WHERE id = $2 AND org_id = $3", text, vehicle_id, org_id)
        n = int(status.split()[-1])
    _profiles.clear()  # every device on this truck reads it again
    return n > 0


async def profile_clear(sub: str) -> bool:
    """"Delete my data": the driver's own stored profile and name go; a fleet truck's profile is the fleet's and stays.
    True when there was one."""
    dev = await device_of(sub)
    _profiles.pop(sub, None)
    if dev is None:
        return False
    k = db.store()
    if k == "sqlite":
        def _clear(con):
            n = con.execute("UPDATE drivers SET profile = NULL WHERE id = ? AND profile IS NOT NULL", (dev.driver_id,)).rowcount
            con.execute("UPDATE drivers SET display_name = NULL WHERE id = ?", (dev.driver_id,))  # the name goes too
            return n
        n = await db._sq_run(_clear)
    else:
        pool = await db._db()
        status = await pool.execute("UPDATE drivers SET profile = NULL WHERE id = $1 AND profile IS NOT NULL", dev.driver_id)
        await pool.execute("UPDATE drivers SET display_name = NULL WHERE id = $1", dev.driver_id)  # the name goes too
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


# ---- org time zone -------------------------------------------------------------------------------------------

DEFAULT_TZ = "America/Los_Angeles"


def valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


async def org_timezone(org_id: str) -> ZoneInfo:
    """The fleet's own time zone, which its "today" is counted in (America/Los_Angeles unless the owner set one)."""
    k = db.store()
    name = None
    if k == "sqlite":
        row = await db._sq_run(lambda con: con.execute("SELECT timezone FROM orgs WHERE id = ?", (org_id,)).fetchone())
        name = row[0] if row else None
    elif k == "pg":
        pool = await db._db()
        name = await pool.fetchval("SELECT timezone FROM orgs WHERE id = $1", org_id)
    return ZoneInfo(name if name and valid_timezone(name) else DEFAULT_TZ)


async def set_org_timezone(org_id: str, name: str) -> None:
    k = db.store()
    if k == "off":
        raise RuntimeError("store is off")
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute("UPDATE orgs SET timezone = ? WHERE id = ?", (name, org_id)))
    else:
        pool = await db._db()
        await pool.execute("UPDATE orgs SET timezone = $1 WHERE id = $2", name, org_id)


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


def forget(sub: str, driver_id: Optional[str] = None) -> None:
    """This worker's copies of a sub that was deleted (the other workers' run out within a minute)."""
    for cache in (_devices, _profiles, _grace, _created, _touched):
        cache.pop(sub, None)
    if driver_id:
        _motive_users.pop(driver_id, None)


def reset_caches() -> None:
    """Tests, and after a bind: forget the per-process copies."""
    _devices.clear()
    _profiles.clear()
    _credentials.clear()
    _motive_users.clear()
    _created.clear()
    _grace.clear()
    _touched.clear()
