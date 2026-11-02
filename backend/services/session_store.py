"""Where a dialog's state lives between turns.

MemorySessionStore: one process, an LRU of 200 DialogState objects (what the copilot did before; dev and tests).
DbSessionStore: the dialog_sessions table (migration 0002), so a second worker or a restarted one continues
the dialog, including a question asked on the previous turn; rows older than TTL_H are swept on save.

SESSION_STORE=memory|db picks one; by default db when DATABASE_URL is set, else memory.

A turn loads its state once (agent.handle, or server.py for /nudges) and publishes it through a contextvar;
everything inside the turn that asks for the session by id (agent._session) gets that same object, and the
turn saves it at the end.
"""
import asyncio
import contextvars
import logging
import os
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

from services import db
from services.dialog_state import DialogState

logger = logging.getLogger("truckbuddy.sessions")

MAX_MEMORY_SESSIONS = 200
TTL_H = 24
SWEEP_EVERY_S = 600

_current: "contextvars.ContextVar[Optional[tuple[str, DialogState]]]" = contextvars.ContextVar("dialog_state", default=None)


class SessionStore(Protocol):
    async def load(self, sid: str) -> DialogState: ...
    async def save(self, sid: str, state: DialogState) -> None: ...
    async def drop_prefix(self, prefix: str) -> int: ...


class MemorySessionStore:
    def __init__(self) -> None:
        self.sessions: "OrderedDict[str, DialogState]" = OrderedDict()

    def get_sync(self, sid: str) -> DialogState:
        if sid not in self.sessions:
            self.sessions[sid] = DialogState()
            while len(self.sessions) > MAX_MEMORY_SESSIONS:
                self.sessions.popitem(last=False)
        self.sessions.move_to_end(sid)
        return self.sessions[sid]

    async def load(self, sid: str) -> DialogState:
        return self.get_sync(sid)

    async def save(self, sid: str, state: DialogState) -> None:
        self.sessions[sid] = state  # the same object was mutated in place; keep it the most recent

    async def drop_prefix(self, prefix: str) -> int:
        gone = [s for s in self.sessions if s.startswith(prefix)]
        for s in gone:
            self.sessions.pop(s, None)
        return len(gone)


class DbSessionStore:
    def __init__(self) -> None:
        self._last_sweep = 0.0

    async def load(self, sid: str) -> DialogState:
        kind = db.store()
        if kind == "sqlite":
            row = await db._sq_run(lambda con: con.execute(
                "SELECT state FROM dialog_sessions WHERE session_id = ?", (sid,)).fetchone())
            text = row[0] if row else None
        else:
            pool = await db._db()
            text = await pool.fetchval("SELECT state::text FROM dialog_sessions WHERE session_id = $1", sid)
        if not text:
            return DialogState()
        try:
            return DialogState.from_json(text)
        except Exception as e:  # noqa: BLE001 - an unreadable row (older schema) is a fresh dialog, never an error
            logger.warning("dialog state for %s unreadable (%s): starting fresh", sid, type(e).__name__)
            return DialogState()

    async def save(self, sid: str, state: DialogState) -> None:
        text = state.to_json()
        now = datetime.now(timezone.utc)
        kind = db.store()
        if kind == "sqlite":
            await db._sq_run(lambda con: con.execute(
                "INSERT INTO dialog_sessions (session_id, driver, state, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET driver = excluded.driver, state = excluded.state, "
                "updated_at = excluded.updated_at", (sid, state.driver, text, now.isoformat())))
        else:
            pool = await db._db()
            await pool.execute(
                "INSERT INTO dialog_sessions (session_id, driver, state, updated_at) VALUES ($1, $2, $3::jsonb, $4) "
                "ON CONFLICT (session_id) DO UPDATE SET driver = EXCLUDED.driver, state = EXCLUDED.state, "
                "updated_at = EXCLUDED.updated_at", sid, state.driver, text, now)
        await self._sweep(now)

    async def _sweep(self, now: datetime) -> None:
        if time.monotonic() - self._last_sweep < SWEEP_EVERY_S:
            return
        self._last_sweep = time.monotonic()
        cutoff = now - timedelta(hours=TTL_H)
        try:
            if db.store() == "sqlite":
                await db._sq_run(lambda con: con.execute("DELETE FROM dialog_sessions WHERE updated_at < ?", (cutoff.isoformat(),)))
            else:
                pool = await db._db()
                await pool.execute("DELETE FROM dialog_sessions WHERE updated_at < $1", cutoff)
        except Exception as e:  # noqa: BLE001
            logger.info("dialog session sweep skipped: %s", e)

    async def drop_prefix(self, prefix: str) -> int:
        if db.store() == "sqlite":
            return await db._sq_run(lambda con: con.execute(
                "DELETE FROM dialog_sessions WHERE session_id LIKE ? || '%'", (prefix,)).rowcount)
        pool = await db._db()
        status = await pool.execute("DELETE FROM dialog_sessions WHERE session_id LIKE $1 || '%'", prefix)
        return int(status.split()[-1])


_memory = MemorySessionStore()
_db_store = DbSessionStore()


def kind() -> str:
    forced = os.environ.get("SESSION_STORE", "").strip().lower()
    if forced in ("memory", "db"):
        return forced
    return "db" if os.environ.get("DATABASE_URL") and db.store() != "off" else "memory"


def get_store() -> SessionStore:
    return _db_store if kind() == "db" else _memory


def memory_sessions() -> "OrderedDict[str, DialogState]":
    """The memory store's dict (tests look sessions up in it)."""
    return _memory.sessions


def enter(sid: Optional[str], state: DialogState):
    """Publish the turn's state; everything in this task tree asking for `sid` gets it."""
    return _current.set((sid, state) if sid else None)


def leave(token) -> None:
    _current.reset(token)


def current(sid: Optional[str]) -> Optional[DialogState]:
    cur = _current.get()
    return cur[1] if cur and sid and cur[0] == sid else None


def current_or_memory(sid: Optional[str]) -> DialogState:
    """The turn's published state for `sid`, else the memory store's (sync; the db store is always loaded by the
    turn first, so outside a turn this falls back to an in-process session)."""
    if not sid:
        return DialogState()
    got = current(sid)
    if got is not None:
        return got
    return _memory.get_sync(sid)


async def drop_prefix(prefix: str) -> int:
    n = await _memory.drop_prefix(prefix)
    if kind() == "db":
        try:
            n += await asyncio.wait_for(_db_store.drop_prefix(prefix), 5)
        except Exception as e:  # noqa: BLE001
            logger.warning("dialog sessions drop failed: %s", e)
    return n
