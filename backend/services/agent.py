"""Copilot agent: GPT-5.5 with tool calling (search_places / set_route).

Route answers are templated and parking directions are fetched by the app afterwards,
so the driver hears the answer fast. Each request is independent.
"""
import asyncio
import json
import logging
import os
import re
import uuid
from typing import Optional

from emergentintegrations.llm.chat import LlmChat, UserMessage

from models import AgentRequest, AgentResponse, ParkingCheck, Place, RouteInfo
from services.geocode import geocode
from services.parking import cached_parking, check_parking, listed_in_data
from services.places import get_places_provider
from services.routing import get_routing_provider

logger = logging.getLogger("truckbuddy.agent")

NOT_UNDERSTOOD = "Sorry, I didn't catch that. Try again."
NO_PLACES = "I couldn't find one nearby"
MODEL = ("openai", "gpt-5.5")
MAX_OPTIONS = 4
MAX_TOOL_ROUNDS = 5
PRECHECK = 3  # nearest candidates satellite-checked before choosing

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_places",
            "description": "Find truck-friendly places near the driver, closest first. "
                           "The search automatically widens the radius once if nothing is close.",
            "parameters": {
                "type": "object",
                "properties": {"category": {"type": "string", "enum": ["truck_stop", "hotel", "rest_area"]}},
                "required": ["category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "route_to_address",
            "description": "Build a truck-safe route (respecting the truck's height, length, weight and hazmat) "
                           "to any US address, city, business or landmark the driver names.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Address or place, e.g. 'Reno, NV' or '1200 Main St, Dallas TX'"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_route",
            "description": "Set the navigation route to a place returned by search_places. Returns the address, "
                           "distance and a satellite check of truck parking with entry and parking directions.",
            "parameters": {
                "type": "object",
                "properties": {"place_id": {"type": "string"}},
                "required": ["place_id"],
            },
        },
    },
]

SYSTEM = """You are TruckBuddy, a hands-free voice copilot for a long-haul truck driver. The driver is driving, so be brief.
Truck: {truck}.
Driver preferences: {prefs}. Routes already apply the toll/ferry/tunnel preferences.

How to act:
- If the driver needs a place, call search_places with the best category yourself, never ask which one:
  truck_stop = fuel, diesel, DEF, food, showers, truck stop;
  hotel = sleep, tired, overnight, bed, motel, hotel, room;
  rest_area = quick break, stretch, restroom, short nap, rest area.
- Then pick the best result and call set_route with its id. Prefer the driver's favorite chains when one is close (within ~5 extra miles), then places whose parking is "listed" or "likely" or tagged with truck parking, and never pick "unlikely" if another option isn't; among equals pick the closest. Always set the route; don't ask for confirmation.
- If search_places returns no places, reply exactly: "I couldn't find one nearby."
- If the driver names a destination (a city, address, business or landmark: "take me to Reno", "go to 1200 Main St Dallas", "I need to get to the Port of Oakland"), call route_to_address with it. The route already accounts for the truck's height, length, weight and hazmat. - If the driver asks something that is not a place request (e.g. "how far is it?", "where do I park?", "thanks"), answer briefly using the context and do NOT call any tool.
- If the input is just a sound or noise label (siren, beeping, music) or is unintelligible, reply exactly: "Sorry, I didn't catch that. Try again."
- Reply in English, spoken style, no markdown, no lists, at most 2 short sentences.
"""


def _place_brief(p: Place) -> dict:
    return {"id": p.id, "name": p.name, "distance_mi": p.distance_mi, "tags": p.tags, "address": p.address,
            "rating": p.rating}


_tasks: set = set()


def _background(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def _street(address: Optional[str]) -> str:
    if not address:
        return ""
    first = address.split(",")[0].strip()
    first = re.sub(r"^\d+[\w-]*\s+", "", first)  # drop the house number
    return f" on {first}" if first and not first[0].isdigit() else ""


def _miles(mi: float) -> str:
    return f"{mi:.1f}" if mi < 10 else f"{mi:,.0f}"


def _route_reply(place: Place, route: RouteInfo) -> str:
    if place.category == "destination":
        dest = place.name.split(",")[0]
        time = route.summary.split(" · ")[0]
        out = f"Route to {dest} is set: {time}, {_miles(route.distance_mi)} miles."
        if route.notices:
            out += f" {route.notices[0]}"
        return out
    return f"Found {place.name}{_street(place.address)}, about {_miles(route.distance_mi)} miles away. Route is set."


def _not_understood(text: str, source: str) -> AgentResponse:
    return AgentResponse(status="not_understood", transcript=text, reply=NOT_UNDERSTOOD,
                         message=NOT_UNDERSTOOD, source=source)


class LlmAgentProvider:
    source = "gpt-5.5"

    async def handle(self, req: AgentRequest) -> AgentResponse:
        text = (req.text or "").strip()
        if not re.search(r"[a-zA-Z]{2,}", text):
            return _not_understood(text, self.source)

        places_p, routing_p = get_places_provider(), get_routing_provider()
        context = f"Driver location: {req.lat:.5f}, {req.lng:.5f}."
        cur = req.active_place
        if cur:
            r = await routing_p.route(req.lat, req.lng, cur.lat, cur.lng, req.truck_profile)
            context += (f" Current route: {cur.name} ({cur.address or 'address unknown'}), "
                        f"{r.distance_mi:.1f} miles, about {r.duration_min} minutes.")
        else:
            context += " No route is active."

        chat = (LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"turn-{uuid.uuid4()}",
                        system_message=SYSTEM.format(truck=req.truck_profile.label, prefs=req.truck_profile.prefs_label))
                .with_model(*MODEL)
                .with_tools(TOOLS, tool_choice="auto"))

        category = None
        searched_empty = False
        candidates: dict[str, Place] = {}
        found: list[Place] = []
        chosen: Optional[Place] = None
        route: Optional[RouteInfo] = None
        parking: Optional[ParkingCheck] = None
        done = False

        try:
            response = await chat.send_message_with_tools(UserMessage(text=f"[{context}]\nDriver said: {text}"))
            for _ in range(MAX_TOOL_ROUNDS):
                if not response.tool_calls:
                    break
                for tc in response.tool_calls:
                    args = tc.arguments or {}
                    if tc.name == "search_places" and args.get("category") in ("truck_stop", "hotel", "rest_area"):
                        category = args["category"]
                        res = await places_p.search(category, req.lat, req.lng)
                        searched_empty = not res.places
                        found = res.places[:MAX_OPTIONS + 2]
                        candidates.update({p.id: p for p in found})
                        # Never wait for satellite checks here: use what's cached and warm the rest in the background.
                        briefs = [_place_brief(p) for p in found]
                        for b, p in zip(briefs, found):
                            c = cached_parking(p)
                            b["parking"] = c.verdict if c else ("listed" if listed_in_data(p) else "not checked")
                        for p in found[:PRECHECK]:
                            _background(check_parking(p))
                        result = {"widened": res.widened, "places": briefs}
                    elif tc.name == "route_to_address" and str(args.get("query", "")).strip():
                        matches = await geocode(str(args["query"]).strip(), req.lat, req.lng)
                        if matches:
                            chosen, parking = matches[0], None
                            route = await routing_p.route(req.lat, req.lng, chosen.lat, chosen.lng, req.truck_profile)
                            found = []
                            done = True
                            break
                        else:
                            result = {"ok": False, "error": "address not found; ask the driver to repeat the destination"}
                    elif tc.name == "set_route":
                        place = candidates.get(str(args.get("place_id", "")))
                        if place:
                            chosen = place
                            # Parking directions follow from the app via /api/parking, so the answer isn't delayed.
                            route = await routing_p.route(req.lat, req.lng, place.lat, place.lng, req.truck_profile)
                            parking = cached_parking(place)
                            done = True
                            break
                        else:
                            result = {"ok": False, "error": "unknown place_id, use an id from search_places"}
                    else:
                        result = {"error": "invalid tool call"}
                    chat.add_tool_result(tc.id, json.dumps(result))
                if done:
                    break
                response = await chat.send_message_with_tools()
            # Route answers are templated: saves a whole LLM round trip.
            reply = _route_reply(chosen, route) if done and chosen and route else (response.content or "").strip()
        except Exception:
            logger.exception("agent LLM failed")
            return _not_understood(text, self.source)

        if chosen and route:
            if not reply:
                reply = f"Found {chosen.name}, about {route.distance_mi:.0f} miles away. Route is set."
            others = [p for p in found if p.id != chosen.id][:MAX_OPTIONS]
            return AgentResponse(status="route_set", transcript=text, category=chosen.category, reply=reply,
                                 place=chosen, route=route, parking=parking, options=others, source=self.source)
        if searched_empty:
            return AgentResponse(status="no_results", transcript=text, category=category,
                                 reply=reply or f"{NO_PLACES}.", message=NO_PLACES, source=self.source)
        if not reply or reply.rstrip(".") == NOT_UNDERSTOOD.rstrip("."):
            return _not_understood(text, self.source)
        return AgentResponse(status="chat", transcript=text, category=category, reply=reply, source=self.source)


_provider = LlmAgentProvider()


def get_agent_provider() -> LlmAgentProvider:
    return _provider
