"""App controls the copilot can perform: trip, sound, map view, truck data and driver preferences.

The model proposes actions; everything is validated here and the spoken confirmation is
built from what actually changed (no confirmation question: changes apply right away).
The app executes the returned actions (the truck profile lives on the device).
"""
import re
from typing import Any, List, Optional, Tuple

from models import AgentRequest, AppAction

PROMPT = """- control: the driver wants the app to DO something or CHANGE a setting. actions = list of {"type": ..., "value": ...}:
  start_navigation (start the ride / "let's go" / "start driving" with a route already set), end_route, show_options (other places found),
  recenter (map back on the truck), overview (show the whole route), zoom_in, zoom_out,
  voice_guidance value=true|false (spoken turn directions), wake_word value=true|false (hands-free wake-word listening), stop_talking (be quiet now),
  set_height_in value=total inches (13 ft 6 in = 162), set_trailer_ft value=feet, set_weight_lb value=pounds (70k = 70000), set_hazmat value=true|false,
  set_driver_name value=name, avoid_tolls value=true|false, avoid_ferries_tunnels value=true|false,
  add_favorite_chain value=chain name, remove_favorite_chain value=chain name, set_break_hours value=hours or null to turn reminders off,
  set_chains_on_board value=true|false (tire chains carried), set_nudges value=true|false ("stop suggesting" turns the copilot's own break/parking/chain suggestions off).
  Several actions are allowed in one turn. Use control (not chat) whenever the driver asks for any of these."""

ROUTE_FIELDS = {"set_height_in", "set_trailer_ft", "set_weight_lb", "set_hazmat", "avoid_tolls", "avoid_ferries_tunnels"}
MAX_CHAINS = 8


def _bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ("true", "on", "yes"):
        return True
    if isinstance(v, str) and v.lower() in ("false", "off", "no"):
        return False
    return None


def _int(v: Any) -> Optional[int]:
    """158 / "158" / "158 inches" / "70,000 lb" -> int."""
    if isinstance(v, bool):
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", "")) if v is not None else None
    return int(round(float(m.group(0)))) if m else None


def _feet(inches: int) -> str:
    ft, inch = divmod(inches, 12)
    return f"{ft} foot {inch}" if inch else f"{ft} feet"


def apply_actions(raw: Any, req: AgentRequest) -> Tuple[List[AppAction], str, bool]:
    """-> (validated actions, spoken reply, end_conversation)."""
    ui = req.ui
    truck = req.truck_profile
    active = req.active_place is not None
    out: List[AppAction] = []
    said: List[str] = []
    changed_data = False
    end = False
    chains = list(truck.favorite_chains)

    def ok(kind: str, value: Any = None, say: str = "", data: bool = False):
        nonlocal changed_data
        out.append(AppAction(type=kind, value=value))
        if say:
            said.append(say)
        changed_data |= data

    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        kind, v = str(a.get("type") or ""), a.get("value")
        b, n = _bool(v), _int(v)
        if kind == "start_navigation":
            if not active:
                said.append("There's no route yet. Where do you want to go?")
            elif ui.driving:
                said.append("We're already rolling.")
            else:
                ok(kind, say="Alright, let's roll")
        elif kind == "end_route":
            ok(kind, say="Okay, route ended") if active else said.append("There's no active route right now.")
        elif kind == "show_options":
            if ui.options_count:
                ok(kind, say="Here are the other options")
            else:
                said.append("I don't have other options right now.")
        elif kind == "overview":
            ok(kind, say="Here's the whole route") if active else said.append("There's no route to show yet.")
        elif kind in ("recenter", "zoom_in", "zoom_out"):
            ok(kind, say={"recenter": "Back on you", "zoom_in": "Zooming in", "zoom_out": "Zooming out"}[kind])
        elif kind == "voice_guidance" and b is not None:
            ok(kind, b, "Okay, I'll call out the turns" if b else "Okay, turn directions are muted")
        elif kind == "wake_word" and b is not None:
            # Wake-word listening is not in this app version: stay honest about how to start a turn.
            ok(kind, b, "Hands-free listening isn't available in this version yet. Tap the copilot when you need me" if b
               else "Okay. Tap the copilot when you need me")
        elif kind == "stop_talking":
            ok(kind, say="Okay")
            end = True
        elif kind == "set_height_in" and n is not None:
            if 60 <= n <= 203:
                ok(kind, n, f"Height is now {_feet(n)}", True)
            else:
                said.append("That height doesn't look right. It has to be between 5 feet and 16 foot 11.")
        elif kind == "set_trailer_ft" and n is not None:
            if 10 <= n <= 65:
                ok(kind, n, f"Trailer is now {n} feet", True)
            else:
                said.append("The trailer has to be between 10 and 65 feet.")
        elif kind == "set_weight_lb" and n is not None:
            if 5000 <= n <= 200000:
                ok(kind, n, f"Weight is now {n:,} pounds", True)
            else:
                said.append("The weight has to be between 5,000 and 200,000 pounds.")
        elif kind == "set_hazmat" and b is not None:
            ok(kind, b, "Hazmat is on, I'll keep you on hazmat-legal roads" if b else "Hazmat is off", True)
        elif kind == "set_driver_name" and isinstance(v, str) and v.strip():
            name = v.strip()[:40]
            ok(kind, name, f"Got it, I'll call you {name.split(' ')[0]}", True)
        elif kind == "avoid_tolls" and b is not None:
            ok(kind, b, "I'll avoid toll roads" if b else "Toll roads are fine now", True)
        elif kind == "avoid_ferries_tunnels" and b is not None:
            ok(kind, b, "I'll avoid ferries and tunnels" if b else "Ferries and tunnels are fine now", True)
        elif kind == "add_favorite_chain" and isinstance(v, str) and v.strip():
            name = v.strip()[:40]
            if any(c.lower() == name.lower() for c in chains):
                said.append(f"{name} is already a favorite.")
            elif len(chains) >= MAX_CHAINS:
                said.append("You already have eight favorites. Remove one first.")
            else:
                chains.append(name)
                ok(kind, name, f"Added {name} to your favorites", True)
        elif kind == "remove_favorite_chain" and isinstance(v, str) and v.strip():
            match = next((c for c in chains if c.lower() == v.strip().lower()), None)
            if match:
                chains.remove(match)
                ok(kind, match, f"Removed {match} from your favorites", True)
            else:
                said.append(f"{v.strip()} isn't in your favorites.")
        elif kind == "set_break_hours":
            if v is None or n == 0 or b is False:
                ok(kind, None, "Break reminders are off", True)
            elif n is not None and 1 <= n <= 14:
                ok(kind, n, f"I'll remind you to take a break every {n} hour{'s' if n > 1 else ''}", True)
            else:
                said.append("Break reminders can be every 1 to 14 hours.")
        elif kind == "set_chains_on_board":
            if b is None:
                said.append("Chains on board, yes or no?")
            else:
                ok(kind, b, "Got it, chains on board" if b else "Got it, no chains on board", True)
        elif kind == "set_nudges":
            if b is None:
                said.append("Suggestions on or off?")
            else:
                ok(kind, b, "I'll speak up with suggestions again" if b else "Okay, I'll stop suggesting things")

    if active and any(a.type in ROUTE_FIELDS for a in out):
        out.append(AppAction(type="reroute"))
        said.append("Rerouting for that")
    if not said:
        return [], "", False
    reply = " ".join(s if s[-1] in ".?!" else f"{s}." for s in said)
    if changed_data and not reply.startswith(("Got it", "Okay")):
        reply = "Done. " + reply
    return out, reply, end
