"""The state of one dialog (one session id), as a model that survives JSON: the copilot keeps it between turns,
and with the database-backed session store any worker can pick the next turn up.

Keys written to the session anywhere (services/agent.py, services/nudges.py, services/breakdown.py, server.py),
all declared below: history, places, city, pending, via, alternatives, rejected, dest_declined, rejected_chains,
confirmed_once, facts, past, past_loaded, n, turns, driver, nudge_break_done, nudge_parking_done,
nudge_chains_done, hos_cache, breakdown_pending, breakdown, providers, providers_at, near, say_learned,
last_offer, replies, name_at, breakdown_asked.
Rules that make it serializable: the former sets (rejected, dest_declined) are lists; the pending question's
"yes" is a pattern NAME (YES_PATTERNS in agent.py), not a compiled regex; the memory read that used to live on
as an asyncio.Task (past_task) is gone, past_loaded says whether it has landed; clocks are epoch seconds
(time.time), never time.monotonic, which differs per process.

The dict-style surface (sess["x"], .get, .setdefault, .pop, "x" in sess) is kept so the many call sites read
as before; the model is what gets stored.
"""
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, PrivateAttr, field_validator

from models import Place, Provider, RouteInfo

_UNSET = object()


def _as_model(cls, v):
    return cls.model_validate(v) if isinstance(v, dict) else v


class DialogState(BaseModel):
    model_config = ConfigDict(extra="allow")

    history: list[str] = []
    places: list[Place] = []
    city: Optional[str] = None
    # {"text", "d", "kind", "no": {...}, "yes": <pattern name>} of a question asked last turn
    pending: Optional[dict] = None
    # {"place": id, "stops": [Place]} stops on the way to the active destination
    via: Optional[dict] = None
    # {"place", "routes": [RouteInfo], "i", "current": RouteInfo|None, "refetch", "origin", "profile"}
    alternatives: Optional[dict] = None
    rejected: list[str] = []
    dest_declined: list[str] = []
    rejected_chains: dict[str, int] = {}
    confirmed_once: bool = False
    facts: Optional[dict] = None
    past: list[str] = []
    past_loaded: bool = False
    n: int = 0
    turns: int = 0
    driver: Optional[str] = None
    nudge_break_done: bool = False
    nudge_parking_done: bool = False
    nudge_chains_done: bool = False
    hos_cache: Optional[list] = None  # [epoch seconds, hos dict]
    breakdown_pending: Optional[dict] = None
    breakdown_asked: Optional[str] = None  # the question asked last turn and not answered (never asked twice in a row)
    breakdown: Optional[dict] = None
    providers: list[Provider] = []
    providers_at: float = 0.0  # epoch seconds
    near: Optional[str] = None
    say_learned: Optional[str] = None
    # {"kind": "stop", "id": stop id, "place": destination id}: what "not that one" turns down when it is not
    # the destination (a stop just added on the way)
    last_offer: Optional[dict] = None
    replies: int = 0  # copilot replies in this dialog
    name_at: Optional[int] = None  # the reply (by `replies`) that last used the driver's name
    # each key as JSON when the state was loaded or last saved: what this request changed is what differs
    _saved: dict = PrivateAttr(default_factory=dict)

    @field_validator("via", mode="before")
    @classmethod
    def _via_models(cls, v):
        if isinstance(v, dict) and isinstance(v.get("stops"), list):
            v = {**v, "stops": [_as_model(Place, s) for s in v["stops"]]}
        return v

    @field_validator("alternatives", mode="before")
    @classmethod
    def _alternative_models(cls, v):
        if isinstance(v, dict):
            v = dict(v)
            if isinstance(v.get("routes"), list):
                v["routes"] = [_as_model(RouteInfo, r) for r in v["routes"]]
            if isinstance(v.get("current"), dict):
                v["current"] = RouteInfo.model_validate(v["current"])
            if isinstance(v.get("origin"), list):
                v["origin"] = tuple(v["origin"])
            if isinstance(v.get("profile"), list):
                v["profile"] = tuple(v["profile"])
        return v

    # ---- dict-style surface -----------------------------------------------------------------------------------

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def __contains__(self, key: str) -> bool:
        """Set to something: a declared field at its default (None, [], {}, False, 0) does not count."""
        if key not in type(self).model_fields:
            return key in (self.__pydantic_extra__ or {})
        value = getattr(self, key)
        default = type(self).model_fields[key].get_default(call_default_factory=True)
        return value is not None and value != default

    def get(self, key: str, default: Any = None) -> Any:
        value = getattr(self, key, _UNSET)
        if value is _UNSET or value is None:
            return default
        return value

    def setdefault(self, key: str, default: Any) -> Any:
        value = getattr(self, key, None)
        if value is None:
            setattr(self, key, default)
            return default
        return value

    def pop(self, key: str, default: Any = _UNSET) -> Any:
        if key in type(self).model_fields:
            value = getattr(self, key)
            setattr(self, key, type(self).model_fields[key].get_default(call_default_factory=True))
            return value
        extra = self.__pydantic_extra__ or {}
        if key in extra:
            return extra.pop(key)
        if default is _UNSET:
            raise KeyError(key)
        return default

    def update(self, other: dict) -> None:
        for k, v in other.items():
            setattr(self, k, v)

    def to_json(self) -> str:
        return self.model_dump_json()

    def __eq__(self, other: object) -> bool:
        """The same dialog: what was saved when (the private baseline) does not count."""
        return isinstance(other, DialogState) and self.model_dump() == other.model_dump()

    def mark_saved(self) -> None:
        """What the store holds now (after a load or a save); changes() is measured from here."""
        self._saved = self.model_dump(mode="json")

    def changes(self) -> dict:
        """The top-level keys this request changed since mark_saved(), as JSON values: all a save writes, so a
        /nudges poll, a breakdown research landing late or a turn running beside another never put back the keys
        the other one changed (review 2026-11-02, item 21)."""
        now = self.model_dump(mode="json")
        return {k: v for k, v in now.items() if k not in self._saved or self._saved[k] != v}

    @classmethod
    def from_json(cls, text: str) -> "DialogState":
        return cls.model_validate_json(text)
