"""What the copilot learns about the driver in a dialog and keeps: the name now and then, rejected chains, "remember
I like Pilot", the facts refresh and the memory intent. LlmAgentProvider inherits these methods."""
import asyncio
import logging
from typing import Optional

from models import AgentRequest, AgentResponse, Place
from services import controls, memory, profile_learn
from services.flags import on
from services.logs import said
from services.agent.common import REJECTIONS_TO_LEARN, _chain_of, _session_if, _short_name
from services.agent.driver_name import NAME_EVERY, name_policy
from services.agent.phrases import ROAD_PREFS, name_like, named_place

logger = logging.getLogger("truckbuddy.agent")


class LearningMixin:
    @staticmethod
    def _name_once_in_a_while(req: AgentRequest, res: AgentResponse) -> None:
        """The driver's first name at most once in NAME_EVERY replies of the dialog (name_policy)."""
        name = (req.truck_profile.driver_name or "").split(" ")[0]
        sess = _session_if(req.session_id)
        n = sess.get("replies", 0) if sess is not None else 0
        last = sess.get("name_at") if sess is not None else None
        if name and res.reply:
            places = [req.active_place, res.place, *res.options, *req.route_stops,
                      *((sess or {}).get("places") or [])]
            res.reply, used = name_policy(res.reply, name, last is None or n - last >= NAME_EVERY, places)
            if used and sess is not None:
                sess["name_at"] = n
        if sess is not None:
            sess["replies"] = n + 1

    @staticmethod
    async def _refresh_facts(driver: str, sess: dict) -> None:
        facts = await profile_learn.refresh_if_due(driver)
        if facts is not None:
            sess["facts"] = facts

    @staticmethod
    async def _note_rejection(sess: dict, place: Place) -> None:
        """A place turned down is a dislike the next dialogs know about (it was read back as a "like", QA
        2026-11-02, bug 5); three "not that one" on the same chain in one dialog: steer clear of the chain."""
        if not on("PROFILE_LEARN"):
            return
        facts = sess.get("facts") or {}
        if place.category != "destination" and _short_name(place).lower() not in \
                [n.lower() for n in facts.get("places_avoided") or []]:
            facts = sess["facts"] = profile_learn.add(facts, avoid_places=[_short_name(place)])
            await profile_learn.save(sess.get("driver"), facts)
        chain = _chain_of(place.name)
        if not chain or not profile_learn.chain_name(chain):  # "Travel Plaza" is a word of the name, not a chain
            return
        counts = sess.setdefault("rejected_chains", {})
        counts[chain] = counts.get(chain, 0) + 1
        avoided = [c.lower() for c in facts.get("chains_avoided") or []]
        if counts[chain] == REJECTIONS_TO_LEARN and chain.lower() not in avoided:
            sess["facts"] = await profile_learn.learn_avoid(sess.get("driver"), facts, chain)
            sess["say_learned"] = profile_learn.LEARNED_AVOID.format(chain=chain).strip()

    async def _remember(self, req: AgentRequest, d: dict, sess: dict, text: str) -> Optional[AgentResponse]:
        """"Remember I like Pilot and I hate tolls": into the learned facts (and saved), and what the profile holds
        (favorite chains, tolls, ferries) into the profile as before. The model calls it a control and only the
        profile changed (QA 2026-11-02, bug 5). None when the words are not a like or a dislike."""
        said_ = profile_learn.said_prefs(text)
        if not said_:
            return None
        add: dict = {k: [] for k in ("like_chains", "like_places", "avoid_chains", "avoid_places", "roads",
                                     "roads_ok", "notes")}
        actions: list[dict] = []
        heard = []
        favorites = {c.lower() for c in req.truck_profile.favorite_chains}
        for verb, what, liked in said_:
            road = next(((name, act) for rx, name, act in ROAD_PREFS if rx.search(what)), None)
            chain = profile_learn.chain_name(what)
            listed = named_place(what, sess["places"])
            if road:
                add["roads_ok" if liked else "roads"].append(road[0])
                if road[1]:
                    actions.append({"type": road[1], "value": not liked})
            elif chain:
                what = chain
                add["like_chains" if liked else "avoid_chains"].append(chain)
                if liked:
                    actions.append({"type": "add_favorite_chain", "value": chain})
                elif chain.lower() in favorites:
                    actions.append({"type": "remove_favorite_chain", "value": chain})
            elif listed or name_like(what):
                add["like_places" if liked else "avoid_places"].append(_short_name(listed) if listed else what)
            else:
                add["notes"].append(f"you {verb} {what}")
            heard.append(f"you {verb} {what}")
        sess["facts"] = profile_learn.add(sess.get("facts"), **add)
        await profile_learn.save(sess.get("driver"), sess["facts"])
        # anything else the model read in the same breath ("...and set my height to 14 feet") still applies
        mine = {a["type"] for a in actions} | {"add_favorite_chain", "remove_favorite_chain"}
        extra = [a for a in d.get("actions") or [] if isinstance(a, dict) and a.get("type") not in mine] \
            if d.get("intent") == "control" else []
        acts, say, _ = controls.apply_actions(extra + actions, req)
        reply = profile_learn.REMEMBERED.format(what=" and ".join(heard))
        if extra and say:
            reply += f" {say}"
        elif any(a.type == "reroute" for a in acts):
            reply += " Rerouting for that."
        logger.info("agent remember %s -> %s", said(text), {k: v for k, v in add.items() if v})
        return AgentResponse(status="chat", transcript=text, reply=reply, actions=acts, source=self.source)

    async def _memory_intent(self, req: AgentRequest, sess: dict, d: dict, text: str) -> AgentResponse:
        """"What do you know about me?" / "forget that"."""
        want = str(d.get("query") or "show").lower()
        if want.startswith("forget"):
            sess["facts"] = None
            sess.pop("past", None)
            driver = sess.get("driver")
            if driver:
                try:
                    await asyncio.wait_for(memory.clear_facts(driver), 3.0)
                except Exception as e:  # noqa: BLE001
                    logger.warning("clear_facts failed: %s", type(e).__name__)
            return AgentResponse(status="chat", transcript=text, reply=profile_learn.FORGOT, source=self.source)
        line = " ".join(x for x in (profile_learn.speech(sess.get("facts"), req.truck_profile),
                                    await self._visits_line(sess.get("driver"))) if x)
        prefs = profile_learn.prefs_line(req.truck_profile)
        if line:
            reply = profile_learn.KNOW.format(facts=line) + (f" {prefs}" if prefs else "")
        elif not profile_learn.is_empty(sess.get("facts")) and prefs:
            reply = prefs  # all it knows is in the profile already
        else:
            # No facts yet (they need ~5 turns) but earlier lines exist: "what's my dog's name?" is answered
            # from them, as it was before facts existed (live test test_long_term_recall_across_sessions).
            reply = await profile_learn.recall(text, sess.get("past") or []) or prefs or profile_learn.KNOW_NOTHING
        return AgentResponse(status="chat", transcript=text, reply=reply, source=self.source)

    @staticmethod
    async def _visits_line(driver: Optional[str]) -> str:
        """The places the driver stopped at often, read when asked (bounded; a slow store only leaves them out)."""
        if not driver:
            return ""
        try:
            return profile_learn.visits_speech(await asyncio.wait_for(memory.visits(driver), 2.0))
        except Exception as e:  # noqa: BLE001 - incl. the store off and a timeout
            logger.info("visits skipped: %s", type(e).__name__)
            return ""
