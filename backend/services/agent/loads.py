"""The owner-operator's ledger by voice: "this load pays 2400" -> read back -> "yes" -> saved with the route's
miles, miles per state, diesel and tolls; "how much did I make this week" -> a sum from the database, worded in
services/ledger.py. A rate, a fix and a delete each go through the confirm question every other change uses
(sess["pending"], status "confirm"), so a misheard number is heard before it is kept.

Nothing here writes a timing note with a number in it: notes become trip events (trip_events._NOTE_KINDS), and a
trip event's numbers reach the fleet owner. The pending kinds ledger_add / ledger_fix / ledger_delete are not in
that map, so only the turn log line sees them, and only as accepted / declined."""
import asyncio
import logging
from typing import Optional

from models import AgentRequest, AgentResponse, Place, RouteInfo
from services import ledger
from services.agent.common import _session_if

logger = logging.getLogger("truckbuddy.agent")

LEDGER_DB_S = 3.0  # a slow store answers "can't reach your ledger", it never holds the turn longer


class LedgerMixin:
    source: str

    @staticmethod
    def _load_snapshot(req: AgentRequest, sess: dict) -> ledger.Snapshot:
        """The route the load is on: the dialog's copy of the route being driven (full miles, miles per state,
        diesel and tolls), else what the app says about it (the miles left), else nothing."""
        snap = ledger.Snapshot(trip_id=req.trip_id)
        place = req.active_place
        if not place:
            return snap
        snap.dest_name, snap.dest_place_id = place.name.split(",")[0].strip(), place.id
        cached = sess.get("alternatives") or {}
        route: Optional[RouteInfo] = cached.get("current") if cached.get("place") == place.id else None
        if route is not None:
            snap.miles, snap.state_miles = route.distance_mi, dict(route.state_mi)
            snap.fuel_usd, snap.tolls_usd = route.fuel_usd, route.tolls_usd
        elif req.active_distance_mi:
            snap.miles = req.active_distance_mi
        return snap

    def _ledger_reply(self, text: str, reply: str, status: str = "chat") -> AgentResponse:
        return AgentResponse(status=status, transcript=text, reply=reply, source=self.source)

    def _ledger_ask(self, sess: dict, text: str, kind: str, d: dict, reply: str, no: str) -> AgentResponse:
        sess["pending"] = {"text": text, "kind": kind, "yes": "ledger_yes", "no": {"reply": no}, "d": d}
        return self._ledger_reply(text, reply, "confirm")

    async def _ledger(self, req: AgentRequest, sess: dict, d: dict, text: str, confirmed: bool) -> AgentResponse:
        """intent=ledger: op add | totals | fix | delete; only for an owner-operator."""
        if not ledger.enabled():
            return self._ledger_reply(text, ledger.OFF)
        if req.truck_profile.operator != "owner":
            return self._ledger_reply(text, ledger.OWNER_ONLY)
        driver = sess.get("driver")
        if not driver:
            return self._ledger_reply(text, ledger.NO_DRIVER)
        op = str(d.get("op") or "add").lower()
        try:
            if op == "totals":
                return await asyncio.wait_for(self._ledger_totals(req, driver, d, text), LEDGER_DB_S + ledger.TZ_LOOKUP_S)
            if op in ("fix", "delete"):
                return await asyncio.wait_for(self._ledger_change(sess, driver, op, d, text, confirmed), LEDGER_DB_S)
            return await asyncio.wait_for(self._ledger_add(req, sess, driver, d, text, confirmed), LEDGER_DB_S)
        except Exception as e:  # noqa: BLE001 - incl. timeout and the store being off
            logger.warning("ledger %s failed: %s", op, type(e).__name__)
            return self._ledger_reply(text, ledger.DOWN)

    async def _ledger_add(self, req: AgentRequest, sess: dict, driver: str, d: dict, text: str,
                          confirmed: bool) -> AgentResponse:
        amount = ledger.amount_of(text, d)
        if amount is None:
            return self._ledger_reply(text, ledger.ASK_AMOUNT)
        snap = self._load_snapshot(req, sess)
        if not confirmed:
            return self._ledger_ask(sess, text, "ledger_add", {"intent": "ledger", "op": "add", "amount": amount},
                                    ledger.ask_save(amount, snap), ledger.NOT_SAVED)
        load_id = await ledger.add(driver, amount, snap)
        logger.info("ledger load saved (miles %s)", "yes" if snap.miles else "no")  # never the amount
        if not snap.dest_name:
            sess["ledger_fill"] = load_id  # the next route this dialog sets fills in the miles
        return self._ledger_reply(text, ledger.saved(amount, snap))

    async def _ledger_totals(self, req: AgentRequest, driver: str, d: dict, text: str) -> AgentResponse:
        period = ledger.period_of(text, d.get("period"))
        zone = await ledger.zone_at(req.lat, req.lng, req.tz)
        start, end = ledger.window(period, zone)
        return self._ledger_reply(text, ledger.reply_totals(await ledger.totals(driver, start, end), period))

    async def _ledger_change(self, sess: dict, driver: str, op: str, d: dict, text: str,
                             confirmed: bool) -> AgentResponse:
        """"That was wrong, it's 2,600" / "delete the last load": the question names the last load, and the "yes"
        changes that one by its id, whatever was saved in between."""
        if confirmed:
            load = {"id": int(d["id"]), "rate_usd": float(d.get("was") or 0), "dest_name": d.get("dest")}
            if op == "delete":
                ok = await ledger.delete(driver, load["id"])
                return self._ledger_reply(text, ledger.deleted(load) if ok else ledger.NOTHING_SAVED)
            amount = float(d["amount"])
            ok = await ledger.set_rate(driver, load["id"], amount)
            return self._ledger_reply(text, ledger.fixed(load, amount) if ok else ledger.NOTHING_SAVED)
        load = await ledger.last(driver)
        if load is None:
            return self._ledger_reply(text, ledger.NOTHING_SAVED)
        about = {"intent": "ledger", "op": op, "id": load["id"], "was": load["rate_usd"], "dest": load.get("dest_name")}
        if op == "delete":
            return self._ledger_ask(sess, text, "ledger_delete", about, ledger.ask_delete(load), ledger.KEPT)
        amount = ledger.amount_of(text, d)
        if amount is None:
            return self._ledger_reply(text, ledger.ASK_AMOUNT)
        return self._ledger_ask(sess, text, "ledger_fix", {**about, "amount": amount}, ledger.ask_fix(load, amount),
                                ledger.KEPT)

    @staticmethod
    def _ledger_fill(req: AgentRequest, place: Place, route: RouteInfo):
        """A load saved before any route: the first destination this dialog routes to is the load's (a truck stop
        or a rest area on the way is not). Returns the update for the caller to run in the background, or None."""
        sess = _session_if(req.session_id)
        if sess is None or not sess.get("ledger_fill") or place.category != "destination":
            return None
        load_id, driver = sess.pop("ledger_fill"), sess.get("driver")
        if not driver or not ledger.enabled():
            return None
        snap = ledger.Snapshot(dest_name=place.name.split(",")[0].strip(), dest_place_id=place.id,
                               miles=route.distance_mi, state_miles=dict(route.state_mi), fuel_usd=route.fuel_usd,
                               tolls_usd=route.tolls_usd, trip_id=req.trip_id)
        return _quietly(ledger.fill(driver, int(load_id), snap))


async def _quietly(coro) -> None:
    """A background write: a failure is logged, never raised into the event loop."""
    try:
        await coro
    except Exception as e:  # noqa: BLE001
        logger.warning("ledger fill failed: %s", type(e).__name__)
