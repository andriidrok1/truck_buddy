"""The owner-operator's ledger: what each load pays, said by voice, with the route's miles, miles per state, diesel
and tolls filled in from the route the copilot set (plan 2026-11-09, "second brain" step 1).

The driver says the rate ("this load pays twenty four hundred"); the copilot reads it back and saves it only after
a "yes". "How much did I make this week?" is a SQL sum over the driver's own rows, worded by a template here: no
number in a ledger answer ever comes from the model.

Privacy: the table (migrations/0011_driver_loads.sql) is keyed by the driver's sub and has no org_id, so no fleet
route can reach it. A rate never goes into trip_events either, where every number passes to the fleet owner
(services/fleet_api.public_event); the delivery mark goes the other way, from the app's "arrived" event to here.
Rows stay until the driver deletes their data (memory.clear), they are not pruned like the trip events.

Only for owner-operators (TruckProfile.operator == "owner"); LEDGER=0 turns it off without a deploy.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from services import db, fuel_index
from services.flags import on

logger = logging.getLogger("truckbuddy.ledger")

MIN_RATE = 100.0  # "twenty four" is 24 or 2,400: below this an amount is a misheard number, not a rate
MAX_RATE = 50_000.0
TZ_LOOKUP_S = 1.0  # the state lookup for the driver's "today" may add at most this much to the reply
DEFAULT_TZ = "America/Chicago"  # the middle of the country when the state is unknown


def enabled() -> bool:
    return on("LEDGER")


# ---- amounts -------------------------------------------------------------------------------------------------

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_SCALES = {"hundred": 100, "thousand": 1000, "k": 1000, "grand": 1000}
_MONEY_AFTER = {"dollars", "dollar", "bucks", "usd"}
_PAY_WORDS = {"pays", "pay", "paying", "paid", "rate", "rates", "is", "it's", "its", "was", "for", "makes", "make",
              "made", "worth"}
_TOKEN = re.compile(r"\$|\d+(?:\.\d+)?|[a-z]+(?:'[a-z]+)?")


@dataclass
class _Run:
    value: float
    start: int  # token index of the first number word
    end: int  # token index after the last one
    marked: bool = False  # "$" before it, or dollars / k / grand in it or after it


def _number(tok: str) -> Optional[float]:
    if re.fullmatch(r"\d+(?:\.\d+)?", tok):
        return float(tok)
    if tok in _UNITS:
        return float(_UNITS[tok])
    if tok in _TENS:
        return float(_TENS[tok])
    return None


def _starts_number(tokens: list[str], i: int) -> bool:
    return _number(tokens[i]) is not None or (tokens[i] == "a" and i + 1 < len(tokens) and tokens[i + 1] in _SCALES)


def _runs(tokens: list[str]) -> list[Optional[_Run]]:
    """Each stretch of number words and digits read as one value; None for a stretch that reads two ways
    ("eighteen fifty": 1,850 or 18 and 50)."""
    out: list[Optional[_Run]] = []
    i = 0
    while i < len(tokens):
        if not _starts_number(tokens, i):
            i += 1
            continue
        start, total, current, ok, marked = i, 0.0, 0.0, True, False
        after = 0  # the scale just applied ("hundred", "thousand"): a smaller number after it adds on
        while i < len(tokens):
            tok = tokens[i]
            n = 1.0 if tok == "a" and _starts_number(tokens, i) else _number(tok)
            if n is not None:
                if after and n < after and (current == 0 or current % after == 0):
                    current += n  # twenty four hundred fifty, two thousand four hundred, 2 thousand 400
                elif i == start:
                    current = n
                elif 20 <= current < 100 and current % 10 == 0 and n < 10 and float(n).is_integer():
                    current += n  # twenty four
                else:
                    ok = False  # eighteen fifty
                after = 0
            elif tok in _SCALES:
                mult = _SCALES[tok]
                marked = marked or tok in ("k", "grand")
                if mult == 100:
                    current = (current or 1) * 100
                else:
                    total += (current or 1) * mult
                    current = 0
                after = mult
            elif tok == "and" and after and i + 1 < len(tokens) and _number(tokens[i + 1]) is not None:
                pass  # "two thousand and fifty"
            else:
                break
            i += 1
        if i < len(tokens) and tokens[i] in _MONEY_AFTER:
            marked = True
        if start > 0 and tokens[start - 1] == "$":
            marked = True
        out.append(_Run(total + current, start, i, marked) if ok else None)
    return out


def parse_amount(text: str) -> Optional[float]:
    """Dollars said for a load: "$2,400", "2400", "2.4k", "twenty four hundred", "two thousand four hundred" ->
    2400.0. None when there is no amount or it could be read two ways: "twenty four" (24 or 2,400), "eighteen
    fifty", or two amounts with nothing to tell the rate ("load 1200 to 500").

    Speech recognition writes numbers either way, so both are read; commas inside digits go first."""
    t = (text or "").lower().replace("-", " ")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)
    tokens = _TOKEN.findall(t)
    runs = _runs(tokens)
    if any(r is None for r in runs):
        return None
    amounts = [r for r in runs if r is not None and MIN_RATE <= r.value <= MAX_RATE]
    # a smaller number that is not an amount ("I-80", "the 2nd") never blocks one; a too-small one alone is no rate
    if not amounts:
        return None
    if len(amounts) == 1:
        return round(amounts[0].value, 2)
    marked = [r for r in amounts if r.marked]
    if len(marked) == 1:
        return round(marked[0].value, 2)
    after_pay = [r for r in amounts if r.start > 0 and tokens[r.start - 1] in _PAY_WORDS]
    if len(after_pay) == 1:
        return round(after_pay[0].value, 2)
    return None


def amount_of(text: str, d: Optional[dict] = None) -> Optional[float]:
    """The rate the driver said, read from the words first; the model's number only when the words have none
    (a language the parser does not read), and only within the same bounds."""
    got = parse_amount(text)
    if got is not None:
        return got
    raw = (d or {}).get("amount")
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    return round(n, 2) if MIN_RATE <= n <= MAX_RATE else None


def money(x: float) -> str:
    """$2,400 for whole dollars, $2,412.50 with cents."""
    return f"${x:,.0f}" if float(x).is_integer() else f"${x:,.2f}"


# ---- periods -------------------------------------------------------------------------------------------------

# Majority time zone per state (a state split between two goes by where most of its freight roads are).
STATE_TZ = {
    "CT": "America/New_York", "DE": "America/New_York", "DC": "America/New_York", "FL": "America/New_York",
    "GA": "America/New_York", "IN": "America/Indiana/Indianapolis", "KY": "America/New_York", "ME": "America/New_York",
    "MD": "America/New_York", "MA": "America/New_York", "MI": "America/Detroit", "NH": "America/New_York",
    "NJ": "America/New_York", "NY": "America/New_York", "NC": "America/New_York", "OH": "America/New_York",
    "PA": "America/New_York", "RI": "America/New_York", "SC": "America/New_York", "VT": "America/New_York",
    "VA": "America/New_York", "WV": "America/New_York",
    "AL": "America/Chicago", "AR": "America/Chicago", "IL": "America/Chicago", "IA": "America/Chicago",
    "KS": "America/Chicago", "LA": "America/Chicago", "MN": "America/Chicago", "MS": "America/Chicago",
    "MO": "America/Chicago", "NE": "America/Chicago", "ND": "America/Chicago", "OK": "America/Chicago",
    "SD": "America/Chicago", "TN": "America/Chicago", "TX": "America/Chicago", "WI": "America/Chicago",
    "AZ": "America/Phoenix", "CO": "America/Denver", "ID": "America/Boise", "MT": "America/Denver",
    "NM": "America/Denver", "UT": "America/Denver", "WY": "America/Denver",
    "CA": "America/Los_Angeles", "NV": "America/Los_Angeles", "OR": "America/Los_Angeles",
    "WA": "America/Los_Angeles", "AK": "America/Anchorage", "HI": "Pacific/Honolulu",
}


def _zone(name: Optional[str]) -> Optional[ZoneInfo]:
    try:
        return ZoneInfo(name) if name else None
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError, ValueError for a malformed name
        return None


async def zone_at(lat: float, lng: float, tz: Optional[str] = None) -> ZoneInfo:
    """The driver's time zone: the app's own when it sends one, else the state the truck is in (cached per
    half-degree cell by fuel_index, bounded by TZ_LOOKUP_S), else America/Chicago."""
    sent = _zone(tz)
    if sent:
        return sent
    try:
        state = await asyncio.wait_for(fuel_index.state_code(lat, lng), TZ_LOOKUP_S)
    except Exception as e:  # noqa: BLE001 - incl. timeout: the default zone answers
        logger.info("ledger time zone lookup skipped: %s", type(e).__name__)
        state = None
    return _zone(STATE_TZ.get(state or "")) or ZoneInfo(DEFAULT_TZ)


PERIODS = ("today", "yesterday", "this week", "last week", "this month", "last month")
_PERIOD_RX = [
    ("yesterday", re.compile(r"\byesterday\b", re.I)),
    ("today", re.compile(r"\b(today|so far today|this day)\b", re.I)),
    ("last week", re.compile(r"\b(last|past|previous) week\b", re.I)),
    ("last month", re.compile(r"\b(last|past|previous) month\b", re.I)),
    ("this month", re.compile(r"\b(this|the) month\b|\bmonth\b", re.I)),
    ("this week", re.compile(r"\bweek\b", re.I)),
]


def period_of(text: str, said: Optional[str] = None) -> str:
    """The period the driver asked about, from the words first, then the model's; this week when neither says."""
    for name, rx in _PERIOD_RX:
        if rx.search(text or ""):
            return name
    return said if said in PERIODS else "this week"


def window(period: str, zone: ZoneInfo, now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """[start, end) of a period in the driver's zone, as UTC; weeks start on Monday."""
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        start, end = day, day + timedelta(days=1)
    elif period == "yesterday":
        start, end = day - timedelta(days=1), day
    elif period in ("this week", "last week"):
        start = day - timedelta(days=day.weekday())
        if period == "last week":
            start -= timedelta(days=7)
        end = start + timedelta(days=7)
    else:
        start = day.replace(day=1)
        if period == "last month":
            start = (start - timedelta(days=1)).replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
    # through the wall clock, so a week across a DST change starts at local midnight, not an hour off
    start = start.replace(tzinfo=None).replace(tzinfo=zone)
    end = end.replace(tzinfo=None).replace(tzinfo=zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


# ---- the store -----------------------------------------------------------------------------------------------

@dataclass
class Snapshot:
    """What the route says about a load when it is saved; every field may be missing (no route yet)."""
    dest_name: Optional[str] = None
    dest_place_id: Optional[str] = None
    miles: Optional[float] = None
    state_miles: dict = field(default_factory=dict)
    fuel_usd: Optional[float] = None
    tolls_usd: Optional[float] = None
    trip_id: Optional[str] = None


_COLS = ("id", "trip_id", "created_at", "rate_usd", "dest_name", "dest_place_id", "miles", "state_miles", "fuel_usd",
         "tolls_usd", "delivered_at")


def _pg(sql: str) -> str:
    """The same statement with Postgres placeholders ($1, $2, ...) for sqlite's ?."""
    n = iter(range(1, 1000))
    return re.sub(r"\?", lambda _: f"${next(n)}", sql)


def _sq_args(args: tuple) -> tuple:
    return tuple(db.iso(a) if isinstance(a, datetime) else a for a in args)


async def _fetch(sql: str, args: tuple) -> list[dict]:
    kind = db.store()
    if kind == "off":
        raise RuntimeError("ledger store is off")
    if kind == "sqlite":
        def _q(con):
            cur = con.execute(sql, _sq_args(args))
            names = [c[0] for c in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]
        return await db._sq_run(_q)
    pool = await db._db()
    return [dict(r) for r in await pool.fetch(_pg(sql), *args)]


async def _execute(sql: str, args: tuple) -> int:
    """Rows changed."""
    kind = db.store()
    if kind == "off":
        raise RuntimeError("ledger store is off")
    if kind == "sqlite":
        return await db._sq_run(lambda con: con.execute(sql, _sq_args(args)).rowcount)
    pool = await db._db()
    status = await pool.execute(_pg(sql), *args)
    return int(status.split()[-1])


def _row(r: dict) -> dict:
    out = dict(r)
    try:
        out["state_miles"] = json.loads(r.get("state_miles") or "{}")
    except ValueError:
        out["state_miles"] = {}
    for k in ("created_at", "delivered_at"):
        if out.get(k) is not None:
            out[k] = db.parse_at(out[k])
    return out


def _state_json(state_miles: dict) -> Optional[str]:
    clean = {str(k): round(float(v), 1) for k, v in (state_miles or {}).items() if v}
    return json.dumps(clean, separators=(",", ":")) if clean else None


def _round(x: Optional[float], nd: int = 1) -> Optional[float]:
    return round(float(x), nd) if x is not None else None


async def add(driver: str, rate_usd: float, snap: Snapshot, at: Optional[datetime] = None) -> int:
    """Saves a load; returns its id."""
    at = at or datetime.now(timezone.utc)
    args = (driver, snap.trip_id, at, float(rate_usd), snap.dest_name, snap.dest_place_id, _round(snap.miles),
            _state_json(snap.state_miles), _round(snap.fuel_usd, 2), _round(snap.tolls_usd, 2))
    cols = "driver, trip_id, created_at, rate_usd, dest_name, dest_place_id, miles, state_miles, fuel_usd, tolls_usd"
    marks = ", ".join("?" for _ in args)
    if db.store() == "pg":
        pool = await db._db()
        return int(await pool.fetchval(_pg(f"INSERT INTO driver_loads ({cols}) VALUES ({marks}) RETURNING id"), *args))
    if db.store() == "off":
        raise RuntimeError("ledger store is off")
    return await db._sq_run(lambda con: con.execute(
        f"INSERT INTO driver_loads ({cols}) VALUES ({marks})", _sq_args(args)).lastrowid)


async def last(driver: str) -> Optional[dict]:
    """The load saved most recently, or None."""
    rows = await _fetch(f"SELECT {', '.join(_COLS)} FROM driver_loads WHERE driver = ? "
                        "ORDER BY created_at DESC, id DESC LIMIT 1", (driver,))
    return _row(rows[0]) if rows else None


async def loads(driver: str, start: datetime, end: datetime) -> list[dict]:
    """The driver's loads saved in [start, end), oldest first."""
    rows = await _fetch(f"SELECT {', '.join(_COLS)} FROM driver_loads WHERE driver = ? AND created_at >= ? "
                        "AND created_at < ? ORDER BY created_at, id", (driver, start, end))
    return [_row(r) for r in rows]


async def set_rate(driver: str, load_id: int, rate_usd: float) -> bool:
    """"That was wrong, it's 2,600": the rate of one of the driver's loads (by id, so a load saved in between
    by another dialog is never the one changed)."""
    return await _execute("UPDATE driver_loads SET rate_usd = ? WHERE id = ? AND driver = ?",
                          (float(rate_usd), int(load_id), driver)) > 0


async def delete(driver: str, load_id: int) -> bool:
    return await _execute("DELETE FROM driver_loads WHERE id = ? AND driver = ?", (int(load_id), driver)) > 0


async def fill(driver: str, load_id: int, snap: Snapshot) -> bool:
    """A load saved before its route was set gets the route's numbers; what it has already stays."""
    return await _execute(
        "UPDATE driver_loads SET dest_name = coalesce(dest_name, ?), dest_place_id = coalesce(dest_place_id, ?), "
        "miles = coalesce(miles, ?), state_miles = coalesce(state_miles, ?), fuel_usd = coalesce(fuel_usd, ?), "
        "tolls_usd = coalesce(tolls_usd, ?), trip_id = coalesce(trip_id, ?) WHERE id = ? AND driver = ?",
        (snap.dest_name, snap.dest_place_id, _round(snap.miles), _state_json(snap.state_miles),
         _round(snap.fuel_usd, 2), _round(snap.tolls_usd, 2), snap.trip_id, int(load_id), driver)) > 0


async def mark_delivered(driver: str, trip_id: str, at: datetime) -> int:
    """The app's "arrived" at the destination of a trip: its loads are delivered (the first arrival counts)."""
    return await _execute("UPDATE driver_loads SET delivered_at = ? WHERE driver = ? AND trip_id = ? "
                          "AND delivered_at IS NULL", (at, driver, trip_id))


@dataclass
class Totals:
    n: int = 0
    gross: float = 0.0
    fuel: Optional[float] = None  # None unless every load has a diesel estimate
    tolls: float = 0.0
    miles: Optional[float] = None  # None unless every load has miles
    no_miles: int = 0

    @property
    def net(self) -> Optional[float]:
        return None if self.fuel is None else self.gross - self.fuel - self.tolls


def sum_up(rows: list[dict]) -> Totals:
    """Plain sums in Python over the period's rows (a week is a handful), so both stores add up the same way."""
    t = Totals(n=len(rows), gross=sum(float(r["rate_usd"]) for r in rows),
               tolls=sum(float(r["tolls_usd"] or 0) for r in rows))
    with_miles = [r for r in rows if r.get("miles")]
    t.no_miles = len(rows) - len(with_miles)
    if rows and not t.no_miles:
        t.miles = sum(float(r["miles"]) for r in rows)
    if rows and all(r.get("fuel_usd") is not None for r in rows):
        t.fuel = sum(float(r["fuel_usd"]) for r in rows)
    return t


async def totals(driver: str, start: datetime, end: datetime) -> Totals:
    return sum_up(await loads(driver, start, end))


# ---- what the copilot says -----------------------------------------------------------------------------------

OWNER_ONLY = "I keep a ledger only for your own truck. Turn on \"My own truck\" in the truck profile if it's yours."
OFF = "I don't keep track of loads yet."
NO_DRIVER = "I can keep your loads only when you're signed in."
DOWN = "I can't reach your ledger right now. Try again in a bit."
ASK_AMOUNT = "How much does the load pay? Say it like \"twenty four hundred dollars\"."
NOT_SAVED = "Okay, not saved."
NOTHING_SAVED = "There's no load saved yet. Tell me what a load pays and I'll keep it."
KEPT = "Okay, I left it as it was."
SAVED_PER_MILE = "Saved. That's {per} a mile before fuel."
SAVED = "Saved."
SAVED_NO_MILES = "Saved. I'll fill the miles when you start a route."
FIXED = "Fixed. The load{to} pays {amount} now."
DELETED = "Deleted the {amount} load{to}."
NONE_IN = "No loads saved {period}. Tell me what a load pays and I'll keep it."


def _to(dest: Optional[str]) -> str:
    return f" to {dest}" if dest else ""


def _mi(miles: float) -> str:
    n = int(round(miles))
    return f"{n} mile" if n == 1 else f"{n:,} miles"


def ask_save(rate: float, snap: Snapshot) -> str:
    """"$2,400 for the load to Reno, 132 miles. Save it?" """
    miles = f", {_mi(snap.miles)}" if snap.miles else ""
    return f"{money(rate)} for the load{_to(snap.dest_name)}{miles}. Save it?"


def saved(rate: float, snap: Snapshot) -> str:
    if snap.miles:
        return SAVED_PER_MILE.format(per=money(round(rate / snap.miles, 2)))
    return SAVED_NO_MILES if not snap.dest_name else SAVED


def ask_fix(load: dict, rate: float) -> str:
    return f"Change the last load{_to(load.get('dest_name'))} from {money(load['rate_usd'])} to {money(rate)}?"


def ask_delete(load: dict) -> str:
    return f"Delete the last load, {money(load['rate_usd'])}{_to(load.get('dest_name'))}?"


def fixed(load: dict, rate: float) -> str:
    return FIXED.format(to=_to(load.get("dest_name")), amount=money(rate))


def deleted(load: dict) -> str:
    return DELETED.format(amount=money(load["rate_usd"]), to=_to(load.get("dest_name")))


def _about(x: float) -> str:
    """Estimates rounded to $10: "about $1,120"."""
    return money(round(x / 10) * 10)


def reply_totals(t: Totals, period: str) -> str:
    """"This week: 3 loads, $6,850. Diesel about $1,120, tolls $46, so about $5,680 net, $2.41 a mile." The
    diesel is the route's estimate at the regional average when the load was saved, so it is "about"."""
    if not t.n:
        return NONE_IN.format(period=period)
    head = f"{period[0].upper()}{period[1:]}: {t.n} load{'s' if t.n != 1 else ''}, {money(round(t.gross))}."
    if t.net is not None:
        tolls = f", tolls {money(round(t.tolls))}" if t.tolls >= 1 else ""
        per = f", {money(round(t.net / t.miles, 2))} a mile" if t.miles else ""
        return f"{head} Diesel about {_about(t.fuel)}{tolls}, so about {_about(t.net)} net{per}."
    if t.miles:
        return f"{head} That's {money(round(t.gross / t.miles, 2))} a mile before fuel."
    if t.no_miles == t.n:
        return head
    return f"{head} {t.no_miles} of them {'has' if t.no_miles == 1 else 'have'} no miles yet."
