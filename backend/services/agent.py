"""Copilot agent: GPT-5.5 with tool calling (search_places / set_route).

Places and routing are still the Phase 1 MOCK providers; the agent chooses among them.
Each request is independent (no conversation memory yet).
"""
import json
import logging
import os
import re
import uuid
from typing import Optional

from emergentintegrations.llm.chat import LlmChat, UserMessage

from models import AgentRequest, AgentResponse, Place, RouteInfo
from services.places import get_places_provider
from services.routing import get_routing_provider

logger = logging.getLogger("truckbuddy.agent")

NOT_UNDERSTOOD = "Sorry, I didn't catch that. Try again."
NO_PLACES = "I couldn't find one nearby"
MODEL = ("openai", "gpt-5.5")

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
            "name": "set_route",
            "description": "Set the navigation route to a place returned by search_places.",
            "parameters": {
                "type": "object",
                "properties": {"place_id": {"type": "string"}},
                "required": ["place_id"],
            },
        },
    },
]

SYSTEM = """You are TruckBuddy, a hands-free voice copilot for a long-haul truck driver. The driver is driving, so be brief.
Truck: {height}, {trailer}.

How to act:
- If the driver needs a place, call search_places with the best category yourself, never ask which one:
  truck_stop = fuel, diesel, DEF, food, showers, truck stop;
  hotel = sleep, tired, overnight, bed, motel, hotel, room;
  rest_area = quick break, stretch, restroom, short nap, rest area.
- Then pick the best result (for hotels prefer places tagged with truck or 53 ft parking, otherwise the closest) and call set_route with its id. Always set the route; don't ask for confirmation.
- If search_places returns no places, reply exactly: "I couldn't find one nearby."
- If the driver asks something that is not a place request (e.g. "how far is it?", "thanks"), answer briefly using the context and do NOT call any tool.
- If the input is just a sound or noise label (siren, beeping, music) or is unintelligible, reply exactly: "Sorry, I didn't catch that. Try again."
- Reply in English, 1-2 short spoken sentences, no markdown, no lists. When a route is set, name the place and distance in miles, e.g. "Found Motel 6 with truck parking, about 4 miles away. Route is set."
"""


def _place_brief(p: Place) -> dict:
    return {"id": p.id, "name": p.name, "distance_mi": p.distance_mi, "tags": p.tags}


class LlmAgentProvider:
    source = "gpt-5.5"

    async def handle(self, req: AgentRequest) -> AgentResponse:
        text = (req.text or "").strip()
        if not re.search(r"[a-zA-Z]{2,}", text):
            return AgentResponse(status="not_understood", transcript=text, reply=NOT_UNDERSTOOD,
                                 message=NOT_UNDERSTOOD, source=self.source)

        places_p, routing_p = get_places_provider(), get_routing_provider()
        context = f"Driver location: {req.lat:.5f}, {req.lng:.5f}."
        if req.active_place_id:
            cur = await places_p.get(req.active_place_id, req.lat, req.lng)
            if cur:
                r = await routing_p.route(req.lat, req.lng, cur.lat, cur.lng)
                context += f" Current route: {cur.name}, {r.distance_mi:.1f} miles, about {r.duration_min} minutes."
        else:
            context += " No route is active."

        chat = (LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"turn-{uuid.uuid4()}",
                        system_message=SYSTEM.format(height=req.truck_profile.height,
                                                     trailer=req.truck_profile.trailer_length))
                .with_model(*MODEL)
                .with_tools(TOOLS, tool_choice="auto"))

        category = None
        searched_empty = False
        chosen: Optional[Place] = None
        route: Optional[RouteInfo] = None

        try:
            response = await chat.send_message_with_tools(UserMessage(text=f"[{context}]\nDriver said: {text}"))
            for _ in range(5):
                if not response.tool_calls:
                    break
                for tc in response.tool_calls:
                    args = tc.arguments or {}
                    if tc.name == "search_places" and args.get("category") in ("truck_stop", "hotel", "rest_area"):
                        category = args["category"]
                        res = await places_p.search(category, req.lat, req.lng)
                        searched_empty = not res.places
                        result = {"widened": res.widened, "places": [_place_brief(p) for p in res.places[:6]]}
                    elif tc.name == "set_route":
                        place = await places_p.get(str(args.get("place_id", "")), req.lat, req.lng)
                        if place:
                            chosen = place
                            route = await routing_p.route(req.lat, req.lng, place.lat, place.lng)
                            result = {"ok": True, "name": place.name, "distance_mi": route.distance_mi,
                                      "duration_min": route.duration_min}
                        else:
                            result = {"ok": False, "error": "unknown place_id"}
                    else:
                        result = {"error": "invalid tool call"}
                    chat.add_tool_result(tc.id, json.dumps(result))
                response = await chat.send_message_with_tools()
            reply = (response.content or "").strip()
        except Exception:
            logger.exception("agent LLM failed")
            return AgentResponse(status="not_understood", transcript=text, reply=NOT_UNDERSTOOD,
                                 message=NOT_UNDERSTOOD, source=self.source)

        if chosen and route:
            if not reply:
                reply = f"Found {chosen.name}, about {route.distance_mi:.0f} miles away. Route is set."
            return AgentResponse(status="route_set", transcript=text, category=chosen.category, reply=reply,
                                 place=chosen, route=route, source=self.source)
        if searched_empty:
            return AgentResponse(status="no_results", transcript=text, category=category,
                                 reply=reply or f"{NO_PLACES}.", message=NO_PLACES, source=self.source)
        if not reply or reply.rstrip(".") == NOT_UNDERSTOOD.rstrip("."):
            return AgentResponse(status="not_understood", transcript=text, reply=NOT_UNDERSTOOD,
                                 message=NOT_UNDERSTOOD, source=self.source)
        return AgentResponse(status="chat", transcript=text, category=category, reply=reply, source=self.source)


_provider = LlmAgentProvider()


def get_agent_provider() -> LlmAgentProvider:
    return _provider
