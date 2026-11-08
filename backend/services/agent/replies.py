"""What the copilot says: the spoken reply templates and the helpers that fill them (where a place is, the
route's warnings, the other way, the diesel price). services/i18n.py matches these templates by their exact English
text, so a line here changes only together with i18n/_build_ru_*.py."""
import re
from typing import Optional

from models import Place, RouteInfo
from services.geocode import _STATES, chain_query, is_street, picked_nearby, town_query
from services.parking import is_town
from services.speech_text import hours_minutes as _minutes_said
from services.agent.phrases import PRICE_WORDS, _ZIP_COUNTRY

NOT_UNDERSTOOD = "Sorry, I didn't catch that. Try again."
ROUTE_DOWN = "I couldn't build a truck route there right now. Try again in a minute."
NO_TRUCK_ROUTE = ("I can't find a truck route there from here. Try another destination, or an address on a main "
                  "road nearby.")
PLACES_DOWN = "Place search is down right now. Give me an address instead."
LLM_DOWN = "Give me a second, I lost my train of thought. Say that again."
SOMETHING_BROKE = "Something went wrong on my end. Try again in a minute."

NO_PLACES = "I couldn't find one nearby"

WHERE_ON = "You're on {street} in {city}, {state}."
WHERE_IN = "You're in {city}, {state}."
WHERE_UNKNOWN = "I can't tell where we are right now."
CHEAPER_OFFER = "There's a way {diff} that saves about ${saves}. Want it?"
ALREADY_BEST = "This is already the {what} way."


def cheaper_offer(extra_s: float, saves: float) -> str:
    """The spoken offer of a cheaper way: how much longer it takes and roughly what it saves (whole dollars)."""
    mins = round(extra_s / 60)
    diff = (f"{_minutes_said(mins)} slower" if mins >= 2 else "about as fast" if mins > -2
            else f"{_minutes_said(-mins)} faster")
    return CHEAPER_OFFER.format(diff=diff, saves=int(round(saves)))


NO_CHAIN_NEARBY = "I don't see a {chain} nearby."
CATEGORY_OFF = "I can't look up {what}s yet. Ask me for a truck stop, a hotel or a rest area."
NO_WALMART = "I couldn't find a Walmart nearby. The closest {what} is {name}, {mi} miles. Want to go there?"

ALL_REJECTED = "That's every one I found nearby. Want me to go back to one of them?"


def heard_not_found(query: str) -> str:
    """Reads back what was heard and offers typing."""
    return f"I heard {query}, but can't find it. Say it again or type it."


_COUNTRY = re.compile(r"^(?:USA|US|United States(?: of America)?)$", re.I)


def _town(address: Optional[str]) -> str:
    """"3645 W Capitol Ave, West Sacramento, CA 95691, USA" -> "West Sacramento, CA"; "Reno, NV, USA" -> "Reno, NV"."""
    parts = [x.strip() for x in (address or "").split(",") if x.strip() and not _COUNTRY.match(x.strip())]
    if len(parts) < 2:
        return ""
    state = parts[-1].split()[0] if parts[-1].split() else ""
    if not re.fullmatch(r"[A-Z]{2}", state):
        return ""
    return f"{parts[-2]}, {state}"


def _in_town(p: Place) -> str:
    town = _town(p.address)
    return f" in {town}" if town and town.split(",")[0].lower() not in p.name.lower() else ""


def _street(address: Optional[str]) -> str:
    """Landmark for speech: the street/exit without the house number, or ''."""
    if not address:
        return ""
    first = address.split(",")[0].strip()
    first = re.sub(r"^\d+[\w-]*\s+", "", first)  # drop the house number
    return first if first and not first[0].isdigit() else ""


def _speak_street(s: str) -> str:
    """'N Pima Rd' -> 'North Pima Road' (whole words, case-insensitive); a '#123' suite is dropped."""
    s = re.sub(r"\s*#\S+", "", s)
    return " ".join(STREET_WORDS.get(w.lower().rstrip("."), w) for w in s.split())


def _label_parts(place: Place) -> tuple[list[str], Optional[str]]:
    """The comma parts of the place's name and address without ZIP and country, and the two-letter state when one
    of the parts carries it ('Payson, Phoenix, AZ 85086, United States' -> (['Payson', 'Phoenix'], 'AZ'))."""
    parts, state = [], None
    for raw in f"{place.name}, {place.address or ''}".split(","):
        p = raw.strip()
        if not p:
            continue
        m = re.match(r"^([A-Za-z]{2})(?:\s+\d{5}(?:-\d{4})?)?$", p)  # "AZ 85086", "Az"
        if m and m.group(1).lower() in _STATES:
            state = state or m.group(1).upper()
            continue
        if _ZIP_COUNTRY.match(p):
            continue
        if p not in parts:
            parts.append(p)
    return parts, state


def spoken_where(place: Place) -> str:
    """Where the place really is, for speech. A town: 'Payson, Arizona'. A street or an address: 'Payson in
    Phoenix, Arizona', '2828 El Centro Road in Sacramento, California'. A business: 'Cave Creek Az on North Pima
    Road in Scottsdale, Arizona'. A name with nothing after it stays as it is ('Reno')."""
    parts, state = _label_parts(place)
    state_name = _STATES.get((state or "").lower(), "").title() if state else ""
    if is_town(place):
        head = parts[0] if parts else place.name.split(",")[0]
        return f"{head}, {state_name}" if state_name else head
    if not parts:
        return place.name.split(",")[0]
    head, rest = re.sub(r"\s*#\S+", "", parts[0]).strip(), parts[1:]  # "Pilot Travel Center #123" -> no number
    if not rest:
        return head
    town = rest[-1]
    street = _speak_street(_street(rest[0]) or rest[0]) if len(rest) > 1 else ""  # no house number on the street
    # a street-type title ("Payson", "2828 El Centro Rd") reads with its words spelled out too
    if is_street(place) or head[:1].isdigit():
        head = _speak_street(head)
    where = f"{head} on {street} in {town}" if street else f"{head} in {town}"
    return f"{where}, {state_name}" if state_name else where


def _tokens(s: str) -> list[str]:
    words = re.findall(r"[a-z0-9']+", s.lower())
    return [w for w in words if w not in ("the", "a", "an")]


def _names_match(query: str, place: Place) -> bool:
    """The letters agree: every word of what was said (before the first comma) is in the place's name, or the
    place's own first part is wholly in what was said. 'Payson' ~ 'Payson, AZ' yes; 'Payson, AZ' ~ 'Cave Creek, Az' no."""
    said = _tokens(query.split(",")[0])
    name = _tokens(place.name)
    first = _tokens(place.name.split(",")[0])
    if not said or not name:
        return False
    return all(w in name for w in said) or (bool(first) and all(w in said for w in first))


def dest_check(query: str, place: Place) -> Optional[str]:
    """Why this geocode should be read back before routing, or None when it is plainly what was said."""
    far = (place.distance_mi or 0) > DEST_FAR_MI
    same = _names_match(query, place)
    if chain_query(query) and is_town(place):
        return "chain_to_town"
    if town_query(query) and not is_town(place) and (is_street(place) or far or not same):
        return "town_to_other"
    if far and not same:
        return "far_other_name"
    if picked_nearby(place):  # "Elm": the geocoder said Elm St, a place near the driver was picked instead
        return "picked_nearby"
    return None


def arrival_street(place: Place, route: RouteInfo) -> str:
    """The street the driver turns off to get in: the last maneuver's street when the route knows it (the
    same one the entry tip on the card names), else the street of the address."""
    ap = route.approach
    if ap and ap.street:
        return re.sub(r"\s*\([^)]*\)", "", ap.street).strip() or _street(place.address)  # "(US-395-BR)" is for the card
    return _street(place.address)


def _miles(mi: float) -> str:
    return f"{mi:.1f}".removesuffix(".0") if mi < 10 else f"{mi:,.0f}"


# (intro, body): the short intro is the first spoken part; variants rotate per dialog.
TEMPLATES = {
    "truck_stop": [
        ("Alright, got one", "{name}{off}, about {mi} miles out."),
        ("Okay", "{name} is about {mi} miles ahead{up}."),
        ("Here's a good one", "{name}{off}, {mi} miles away."),
        ("Found you a stop", "{name}{up}, about {mi} miles."),
    ],
    "hotel": [
        ("Alright, found you a bed", "{name}{off}, about {mi} miles out."),
        ("Okay", "{name} is about {mi} miles ahead{up}."),
        ("Here's a good one for the night", "{name}{off}, {mi} miles away."),
        ("Got a place for you", "{name}{up}, about {mi} miles."),
    ],
    "rest_area": [
        ("Alright, got a spot to stretch", "{name}{off}, about {mi} miles out."),
        ("Okay", "{name} is about {mi} miles ahead{up}."),
        ("Here's a good one", "{name}{off}, {mi} miles away."),
        ("Found you a break", "{name}{up}, about {mi} miles."),
    ],
    "destination": [
        ("Alright", "Route to {dest} is set, {time}, about {mi} miles."),
        ("Okay, we're headed to {dest}", "That's {time}, {mi} miles."),
        ("Got it", "{dest} it is, {time} and {mi} miles."),
    ],
    "walmart": [
        ("Found a Walmart", "{name}{off}, about {mi} miles out. Big lot, but overnight is up to the store manager."),
        ("Okay", "{name} is about {mi} miles ahead{up}. Ask inside before you stay the night."),
        ("Here's one", "{name}{off}, {mi} miles away. Check with the store about parking overnight."),
    ],
    "cat_scale": [
        ("Got a scale for you", "CAT scale at {name}{off}, about {mi} miles out."),
        ("Okay", "Nearest CAT scale is at {name}, about {mi} miles ahead{up}."),
        ("Here's one", "CAT scale at {name}{off}, {mi} miles away."),
    ],
    "truck_wash": [
        ("Found a wash", "{name}{off}, about {mi} miles out."),
        ("Okay", "{name} is about {mi} miles ahead{up}."),
        ("Here's one", "{name}{off}, {mi} miles away."),
    ],
    "weigh_station": [
        ("Got one", "{name}{off}, about {mi} miles out. Open or closed, watch the sign."),
        ("Okay", "{name} is about {mi} miles ahead{up}. I can't see if it's open."),
        ("Here's the nearest", "{name}{off}, {mi} miles away. Check the sign for open or closed."),
    ],
    # truck_stop results that are really gas stations: honest wording.
    "fuel_stop": [
        ("Closest I've got is a fuel stop", "{name}{off}, about {mi} miles out, might be tight for a rig."),
        ("Okay, it's a fuel stop, not a truck stop", "{name} is about {mi} miles ahead{up}."),
    ],
}
NOTICES = (
    ("toll", "Heads up, couldn't dodge all the tolls on this one."),
    ("restricted", "Heads up, part of this route might be tight for your rig, so watch the signs."),
    ("ferry", "Heads up, there's a ferry on this one."),
    ("backup route", "Heads up, the truck router is down, so this is a backup route with no turn prompts. Follow the line on the map."),
)
CLOSURE = "Heads up, there's a road closure on this route: {description}."
ALT_REPLY = "Here's another way{via}, {time}, {mi} miles{tolls}."
NO_ALT = "That's the only truck route I've got."
TILL_SAID = ", in effect till "  # the warning line already names the end: the detour line must not repeat it

CHAINS_NO_ROUTE = "Set a route and I'll check Caltrans chain controls along it."
CHAINS_OUTSIDE = "I only have chain controls for California, and this route doesn't go through it."
CHAINS_NONE = "No chain controls on your route right now."
CHAINS_DOWN = "I can't check chain controls on your route right now. Try again in a minute."
ALT_NO_ROUTE = "Set a route first and I'll look for other ways."
VIA_ADDED = "Added {name}{ahead}. New total {time}, {mi} miles{dwell}."
VIA_DWELL = ", counting {minutes} minutes at {each}"  # HERE adds VIA_STOP_S per stop; the total looked wrong without it (H9)
VIA_NO_ROUTE = "Set a route first, then I can add stops on the way."
VIA_FULL = "I can only add three stops. Say 'clear the stops' to start over."
VIA_DUPLICATE = "{name} is already one of your stops."
VIA_IS_DESTINATION = "That's where we're headed, {name}."
VIA_STRAIGHT = "Straight there, {time}, {mi} miles."
VIA_CLEARED = "Stops cleared. " + VIA_STRAIGHT
VIA_NONE = "There are no stops on this route."
VIA_NONE_AHEAD = "I couldn't find a {what} ahead on your route."
VIA_NO_OTHER = "I couldn't find another {what} ahead on your route."
VIA_REJECTED = "Okay, not {name}. "
VIA_DROPPED = "Okay, no stop at {name}. I couldn't find another {what} on the way. "
VIA_LEFT = "New total {time}, {mi} miles."
VIA_SKIPPED = "Okay, no stop at {name}. "
VIA_NOT_A_STOP = "{name} isn't one of your stops."
VIA_DEST_NOT_A_STOP = "{name} is where we're headed, not a stop. Your stops stay."
VIA_WHICH = "Which one, {names}?"
# A stop that is nowhere near the way (the geocoder put "the Pilot in Fernley" in Wells, 300 miles past Reno,
# and the dialog added it without a word, 2026-11-01): ask before the route changes.
VIA_FAR = "{name} is about {extra} out of the way; from here that's {new} instead of {old}. Add it anyway?"
VIA_FAR_NO = "Okay, no stop added."
# A geocode that may not be what the driver meant ("Payson, AZ" -> a street in Phoenix or a business in Cave Creek,
# "Pilot" -> the hamlet of Pilot, NV; stand 2026-11-01): the reply names where the place really is, and when the
# match is far or named differently the copilot asks before the route is built.
DEST_FAR_MI = 50.0
DEST_CHECK = "I found {where}, about {mi} miles away. Is that the one?"
DEST_NO = "Okay, not that one. Say the town and state, or type the address."
DEST_ONLY = "That's the only match I have for {said}. Try the town with its state, or type the address."

STREET_WORDS = {"st": "Street", "ave": "Avenue", "rd": "Road", "blvd": "Boulevard", "dr": "Drive", "hwy": "Highway",
                "fwy": "Freeway", "pkwy": "Parkway", "ln": "Lane", "ct": "Court", "pl": "Place", "trl": "Trail",
                "cir": "Circle", "n": "North", "s": "South", "e": "East", "w": "West", "ne": "Northeast",
                "nw": "Northwest", "se": "Southeast", "sw": "Southwest"}

WHICH_PLACE = "Which place? Find one or set a route first."
# Low-confidence transcripts of a destination or a place name get one "Did you say ...?" before anything is acted on.
CONFIRM = "Did you say {what}?"
CONFIRM_NO = "Okay, say it again."


def _alt_reply(route: RouteInfo, current: Optional[RouteInfo] = None) -> str:
    """"Here's another way via I-80 and CA-89, 4 h 33 min, 204 miles, about 1 h 42 min longer.": the difference
    to the route the driver is on is what decides, so it is said when it is five minutes or more."""
    via = f" via {' and '.join(route.labels[:2])}" if route.labels else ""
    tolls = f", about ${route.tolls_usd:,.0f} in tolls" if route.tolls_usd else ""
    diff = ""
    if current is not None:
        d = route.duration_min - current.duration_min
        if abs(d) >= 5:
            diff = f", about {_minutes_said(abs(d))} {'longer' if d > 0 else 'faster'}"
        if route.cost_usd is not None and current.cost_usd is not None:
            saves = round(current.cost_usd - route.cost_usd)
            if abs(saves) >= 5:
                diff += f", about ${abs(saves)} {'cheaper' if saves > 0 else 'more'} in fuel and tolls"
        # The weather is why most drivers ask: say when the other way misses it, or runs into it. Only
        # a completed lookup counts as clear; an unknown road gets no claim of being clear. Weather the new
        # road is known to have is said whatever we know about the old one (QA review 2026-11-01).
        on_current = {a.id for a in current.weather}
        new_weather = [a for a in route.weather if a.id not in on_current]
        if current.weather and route.weather_known and not route.weather:
            diff += f", and it misses the {current.weather[0].event}"
        elif new_weather:
            diff += f", but it runs into a {new_weather[0].event}"
    return ALT_REPLY.format(via=via, time=route.summary.split(" · ")[0], mi=_miles(route.distance_mi), tolls=tolls + diff)


def _notice(raw: str) -> str:
    low = raw.lower()
    return next((say for key, say in NOTICES if key in low), "Heads up, check this route for restrictions.")


RESTRICTED_SPEECH = ("Heads up, I couldn't find a legal truck route to there. {line} Don't take this one blind: "
                     "check the signs, or give me another destination.")
LEGAL_SPEECH = "Heads up: {line}"


def _route_warning(route: RouteInfo) -> str:
    """One spoken heads-up for a new route. A route that breaks the truck's restrictions (the router found
    no compliant one) comes first and in full, with the limit and the truck's own number; then a closure;
    then a router notice; nothing when there's none of those."""
    closed = next((i for i in route.incidents if i.closed), None)
    closure = CLOSURE.format(description=closed.description) if closed else ""
    if route.restricted and route.notices:
        line = route.notices[0]
        said = (LEGAL_SPEECH if route.restricted == "legal" else RESTRICTED_SPEECH).format(line=line)
        return f"{said} {closure}" if closure else said  # a closure on top of a restriction is still said
    if closure:
        return closure
    return _notice(route.notices[0]) if route.notices else ""


def _with_warnings(reply: str, route: RouteInfo, *lines: Optional[str]) -> str:
    """A route answer followed by what the driver must hear about that route: the restriction / closure / router
    notice warning, then any further lines (chain controls, weather, a preference just learned). Every answer that
    builds a route ends through here: "park near my delivery in Stockton" said only the name and the miles over a
    route under a low bridge, and the stop answers said nothing either (review 2026-11-02, bug 14)."""
    return " ".join(p.strip() for p in (reply, _route_warning(route), *lines) if p and p.strip())


def _diesel_note(p: Place) -> str:
    """Price tag for the model's place list, so "the cheapest diesel" resolves to a place_index."""
    if p.diesel_price is None:
        return ""
    return f", diesel ${p.diesel_price:.2f}" + ("" if p.diesel_kind == "truck" else " (car pumps)")


NO_PRICE = " I don't have a diesel price for it."
NO_PRICES = "None of these list a diesel price, so I can't tell which is cheapest."
# "I can check the cheapest one in the list if you want" was an offer of nothing (QA 2026-11-02): the closest is
# what there is to offer, and "yes" goes there.
NO_PRICES_OFFER = NO_PRICES + " The closest is {name}, {mi} miles. Want to go there?"
NO_PRICES_KEEP = NO_PRICES + " {name} is the closest, and that's where we're headed."

ALREADY_CHEAPEST = "{name} is the cheapest on the list, diesel at ${price:.2f}, and that's where we're headed."


def price_caveat(text: str, place: Optional[Place]) -> str:
    """The driver asked about the price and the place has none: say so instead of going quiet about it."""
    if not place or place.category != "truck_stop" or place.diesel_price is not None:
        return ""
    return NO_PRICE if PRICE_WORDS.search(text or "") else ""


def _diesel_sentence(place: Place, regional: str = "") -> str:
    """Spoken price for a fuel-type place (+ the regional comparison when known); nothing without a fresh price."""
    if place.diesel_price is None or place.category != "truck_stop":
        return ""
    if place.diesel_kind == "truck":
        return f" Diesel's ${place.diesel_price:.2f} there.{regional}"
    return f" Diesel's ${place.diesel_price:.2f} at the car pumps, truck lanes may differ.{regional}"


def _rating_note(p: Place) -> str:
    """For the model's place list: ", 4.3 stars (120 reviews)" so "is it any good?" has something to go on."""
    if p.rating is None:
        return ""
    return f", {p.rating:.1f} stars" + (f" ({p.rating_count} reviews)" if p.rating_count else "")
