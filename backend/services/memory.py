"""Long-term conversation memory per driver (MongoDB), kept until the driver clears it."""
import os
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorClient

_client = AsyncIOMotorClient(os.environ["MONGO_URL"])
_col = _client[os.environ["DB_NAME"]]["conversation"]
_indexed = False


async def _ensure_index() -> None:
    global _indexed
    if not _indexed:
        await _col.create_index([("driver", 1), ("at", 1)])
        _indexed = True


async def add(driver: str, turns: list[tuple[str, str]]) -> None:
    """turns: [(role, text)], role is "driver" or "copilot"."""
    now = datetime.now(timezone.utc)  # +1 ms per turn keeps the order (Mongo dates have ms precision)
    docs = [{"driver": driver, "role": r, "text": t, "at": now + timedelta(milliseconds=i)}
            for i, (r, t) in enumerate(turns) if t]
    if docs:
        await _ensure_index()
        await _col.insert_many(docs)


async def recent(driver: str, limit: int) -> list[dict]:
    """Last `limit` turns, oldest first."""
    await _ensure_index()
    docs = await _col.find({"driver": driver}, {"_id": 0, "driver": 0}).sort("at", -1).limit(limit).to_list(limit)
    docs.reverse()
    for d in docs:
        d["at"] = d["at"].replace(tzinfo=timezone.utc).isoformat()
    return docs


async def clear(driver: str) -> int:
    return (await _col.delete_many({"driver": driver})).deleted_count
