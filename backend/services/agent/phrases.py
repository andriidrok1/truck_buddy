"""What the driver's words mean before (or instead of) the classifier: control phrases, yes/no answers,
references into the list of places, stops named or dropped, and the classifier mistakes the words overrule."""
import logging
import re
from typing import Optional

from models import Place
from services.geo import haversine_km, km_to_mi
from services.logs import said

logger = logging.getLogger("truckbuddy.agent")

# Control phrases the model sometimes returns as a destination (or only as a reply).
_CONTROL_PHRASES: list[tuple[re.Pattern, dict]] = [
    (re.compile(r"\b(?:start|begin)\s+(?:the\s+)?(?:route|ride|trip|navigation|driving)\b|\blet'?s\s+go\b|"
                r"\bgo\s+ahead\b|\bthe\s+stars\s+are\s+out\b", re.I), {"type": "start_navigation"}),
    (re.compile(r"\b(?:stop|end|cancel)\s+(?:the\s+)?(?:route|ride|trip|navigation)\b", re.I), {"type": "end_route"}),
    (re.compile(r"\bturn\s+(?:the\s+)?directions\s+off\b|\bturn\s+off\s+(?:the\s+)?directions\b|\bmute\b", re.I),
     {"type": "voice_guidance", "value": False}),
    (re.compile(r"\bturn\s+(?:the\s+)?directions\s+on\b|\bturn\s+on\s+(?:the\s+)?directions\b", re.I),
     {"type": "voice_guidance", "value": True}),
    (re.compile(r"\bre-?center\b", re.I), {"type": "recenter"}),
]


def control_phrase(text: str) -> Optional[dict]:
    """The control a phrase asks for, if it is only a control (no digits, no place-like proper nouns)."""
    if not text or re.search(r"\d", text):
        return None
    for rx, action in _CONTROL_PHRASES:
        m = rx.search(text)
        if not m:
            continue
        rest = (text[:m.start()] + " " + text[m.end():]).strip(" .,!?")
        words = rest.split()
        if len(words) <= 3 and not any(w[0].isupper() for w in words[1:] if w[0].isalpha()):
            return action
    return None


PARK_WORDS = re.compile(r"\b(park|parking|overnight|shut ?down|lay ?over|sleep in (?:the|my) (?:truck|cab|sleeper))\b", re.I)
# No "shower": it is a truck-stop thing in the prompt, so "park tonight, with a shower" is still a truck stop.
BED_WORDS = re.compile(r"\b(hotel|motel|room|bed|inn|lodge)\b", re.I)
# The same pair in Russian: "где встать на ночь" is where to put the truck, and it went to a motel (QA 2026-11-11).
# Parking and overnight words count on their own; "встать", "стоять", "остановиться" only with the night next to
# them ("устал стоять в пробке" is not parking); sleeping counts only in the truck or the cab.
PARK_WORDS_RU = re.compile(
    r"\b(?:стоянк\w*|парков\w*|паркинг\w*|(?:при|за)парков\w*|ноч[её]вк\w*|(?:пере|за)?ноч(?:ева|у[юе])\w*"
    r"|(?:в?стать|встан\w+|(?:по)?стоять|остановиться|остановлюсь)\b[^.?!]{0,25}?\b(?:на ночь|ночью|до утра)"
    r"|(?:по)?спать\b[^.?!]{0,15}?\bв (?:траке|кабине|спальнике|машине))\b", re.I)
BED_WORDS_RU = re.compile(r"\b(?:отел\w*|мотел\w*|гостиниц\w*|хостел\w*|номер\w*|кроват\w*|койк\w*|постел\w*)\b", re.I)
# A driver who wants a shower (QA 2026-11-11: a bare "душ" came back unclear). Paid showers are a truck-stop thing.
# Only the reflexive verbs: "помыть трак" is a truck wash, "помыться" is the driver. "Rain showers" is weather.
SHOWER_WORDS = re.compile(
    r"\b(?:showers?|wash up|freshen up|душ\w*|помыть?ся|помоюсь|вымыть?ся|искупаться|сполоснуться"
    r"|помит(?:и|ь)ся|помитись|помиюсь)\b", re.I)
SHOWER_NOT = re.compile(r"\b(?:rain|snow|thunder\w*|scattered|isolated|chance of|дожд\w*|ливн\w*|душн\w*)\b", re.I)
# A question or a request, not small talk about a shower: "where can I shower", "душ", "нужен душ", "хочу помыться".
SHOWER_ASK = re.compile(
    r"^\W*(?:душ|shower)\W*$|\b(?:where|need|want|find|get|take|any|где|нужен|нужна|нужно|надо|хочу|найди|есть|де|потрібен)\b",
    re.I)


def asks_for_shower(text: str) -> bool:
    """The driver asks where to wash: "душ", "где помыться", "where can I take a shower" (not "rain showers")."""
    return bool(SHOWER_WORDS.search(text or "")) and not SHOWER_NOT.search(text or "")


_STREET_ONLY = re.compile(r"^\s*\d+[a-z]?\s+[\w .'-]+$", re.I)  # "2828 El Centro Rd" (no town after a comma)
_ZIP_COUNTRY = re.compile(r"^\s*(?:[A-Z]{2}(?:\s+\d{5}(?:-\d{4})?)?|\d{5}|United States|USA)\s*$")


def with_city(query: str, city: Optional[str]) -> str:
    """Street address alone after a turn that named a city: search it in that city."""
    return f"{query}, {city}" if city and _STREET_ONLY.match(query) else query


def city_of(query: str, place: Place) -> Optional[str]:
    """Town named in this turn ("Reno, NV" or "..., Sacramento, CA"), for a follow-up street address."""
    parts = [p.strip() for p in query.split(",") if p.strip()]
    if parts and not re.search(r"\d", parts[0]):  # a town ("Reno, NV"), not a landmark that geocoded elsewhere
        return query if place.name.split(",")[0].strip().lower() == parts[0].lower() else None
    if len(parts) > 1 and re.search(r"\d", parts[0]):
        rest = [p for p in parts[1:] if not _ZIP_COUNTRY.match(p)]
        return ", ".join(rest) or None
    return None


GOODBYE = re.compile(r"\b(bye|goodbye|that's all|that is all|that's it|i'm good|im good|no thanks|nothing else|see you)\b", re.I)

CHAIN_QUESTION = re.compile(r"\bchain[ -]?(?:control|law|requirement|restriction)s?\b|\bchains?\s+(?:ahead|required|needed|on the (?:pass|route|way)|up ahead)\b|\b(?:any|are there|is there|do I need)\b.*\bchains?\b", re.I)

# Russian and Ukrainian answers too (services/i18n.py): a "да" to "Did you say Reno?" went to the classifier as a new
# request and the question was lost (review 2026-11-05).
YES_RU_UK = r"да|ага|угу|давай|конечно|точно|верно|правильно|так точно|ок(?:ей)?|хорошо|так|авжеж|звісно|добре|вірно"
NO_RU_UK = r"нет|неа|не надо|не то|не туда|ні|нє|не треба"
DEST_YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|correct|right|that's (?:it|right|the one)|that one|"
                      r"go ahead|do it|take me there|fine|туда|поехали|вези|" + YES_RU_UK + r")\b", re.I)

VIA_FAR_YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|add it|go ahead|do it|please do|fine|добавь|добавляй|"
                         r"додай|" + YES_RU_UK + r")\b", re.I)
# A pending question names its extra "yes" pattern instead of carrying the regex (the state is stored as JSON).
YES_PATTERNS = {"via_far_yes": VIA_FAR_YES, "dest_yes": DEST_YES}

YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|correct|right|that's right|that's it|sure|affirmative|" + YES_RU_UK + r")\b",
                 re.I)
NO = re.compile(r"^\s*(?:no|nope|nah|wrong|that's wrong|not that|negative|" + NO_RU_UK + r")\b", re.I)
# Words that may follow a yes or a no and still leave it a plain answer ("yeah, that's it", "nope, not that one").
# Anything else is a new request in the same breath ("No, take me to Fresno", "Right, actually find me a truck
# stop") and goes to the classifier: the first was answered "say it again", the second drove to the pending
# destination (review 2026-11-02, bug 17).
ANSWER_FILLER = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "right", "correct", "exactly", "affirmative", "no",
                 "nope", "nah", "negative", "not", "wrong", "that", "that's", "thats", "this", "it", "is", "one", "the",
                 "please", "thanks", "thank", "you", "go", "ahead", "do", "take", "me", "there", "add", "and", "let's",
                 "lets", "sounds", "good", "great", "fine", "perfect", "cool", "man", "buddy", "sir", "boss",
                 # Russian / Ukrainian
                 "да", "ага", "угу", "давай", "конечно", "точно", "верно", "правильно", "хорошо", "ок", "окей", "так",
                 "нет", "неа", "не", "надо", "то", "это", "оно", "он", "она", "туда", "поехали", "вези", "добавь",
                 "добавляй", "пожалуйста", "спасибо", "брат", "братан", "шеф", "отлично", "супер", "норм", "нормально",
                 "ні", "нє", "треба", "авжеж", "звісно", "добре", "вірно", "додай", "дякую", "будь", "ласка"}


def bare_answer(text: str, rx: re.Pattern) -> bool:
    """The pattern answers the question and nothing but filler comes after it."""
    m = rx.match(text or "")
    if not m:
        return False
    # words of any script: "нет, поехали во Фресно" is a new request, not a bare "no"
    return all(w in ANSWER_FILLER for w in re.findall(r"[^\W\d_']+(?:'[^\W\d_]+)?", text[m.end():].lower()))


PRICE_WORDS = re.compile(r"\b(cheap(?:est|er)?|price|prices|cost|how much)\b", re.I)

CHEAPEST = re.compile(r"\bcheap(?:est|er)\b", re.I)
FUEL_WORDS = re.compile(r"\b(?:diesel|fuel|gas|def)\b", re.I)

# "Where's the cheapest diesel" asks which listed place is cheapest; "is diesel cheaper in Oregon" and "fuel was
# cheaper yesterday huh" are talk, and the list answered them by moving the route (review 2026-11-02).
CHEAPEST_ASK = re.compile(
    r"\b(?:where|which|what|who)(?:'?s|'?re| is| are| one| has| place)?\b[^.!?]*\bcheapest\b"  # a question
    r"|^(?:(?:ok(?:ay)?|so|and|just|please|alright)[, ]+)*(?:(?:take|get|route) me to |go to |find (?:me )?|"
    r"give me |show me |let'?s (?:go to |do )?|i (?:want|need) )?(?:the )?cheapest\b"  # "cheapest diesel", "take me to the cheapest"
    r"|\bthe cheapest (?:one|place|stop)\b", re.I)
# "the cheapest diesel in Reno": another place than the list's, the model's search is the answer
ELSEWHERE = re.compile(r"\b(?:in|near|around|at|by|toward|towards|outside|across)\s+(?:the\s+)?(?P<w>[a-z0-9']+)", re.I)
NOT_ELSEWHERE = {"here", "there", "list", "area", "town", "city", "one", "ones", "place", "places", "stop", "stops",
                 "them", "it", "these", "those", "this", "that", "moment", "minute", "least", "all", "my", "our"}
CHEAPEST_NEAR_MI = 30.0  # a list whose nearest place is farther than this from the truck was searched somewhere else


def asks_cheapest(text: str, places: list[Place]) -> bool:
    """The driver asks which listed place is cheapest and names no other place or region."""
    if not CHEAPEST_ASK.search(text or ""):
        return False
    listed = set().union(*(_name_tokens(p.name) for p in places)) if places else set()
    return not any(m.group("w").lower() not in NOT_ELSEWHERE and not (_name_tokens(m.group("w")) & listed)
                   for m in ELSEWHERE.finditer(text))


def list_is_near(lat: float, lng: float, places: list[Place]) -> bool:
    """The list was found around where the truck is now (it is not a list from 150 miles back)."""
    return bool(places) and km_to_mi(min(haversine_km(lat, lng, p.lat, p.lng) for p in places)) <= CHEAPEST_NEAR_MI


# A fuel/rest stop asked for "on my route" while a route is active is a stop on the way, never a new destination.
ON_THE_WAY = re.compile(r"\b(?:on|along) (?:my|the|our) (?:route|way)\b|\ben route\b|\bon the way\b"
                        r"|\bbefore (?:I|we) (?:get|arrive)\b", re.I)
STOP_CATEGORIES = {"truck_stop", "rest_area", "cat_scale", "truck_wash", "weigh_station", "walmart"}

# "Not that one" and friends: the driver turns a PLACE down. With a route active the classifier sometimes
# reads it as "another road" (it sees "N alternative routes not yet offered"); the words decide instead.
REJECT_PLACE = re.compile(
    r"\b(?:not (?:that|this) one|something else|somewhere else|another one|a different one|different one|"
    r"(?:the )?next one|not there|no,? not that)\b", re.I)
# "Skip the Pilot", "drop the Love's stop", "take the Pilot off": an order about one of the route's stops. The verb
# leads the sentence and the stop is its object; "can I drop my trailer at the Pilot", "what time should I take off
# from the Pilot" and "drop the load in Fernley" dropped the stop too (review 2026-11-02).
DROP_ORDER = re.compile(
    r"^(?:(?:ok(?:ay)?|yeah|yes|no|so|and|please|let'?s|just|go ahead and|can you|could you|would you|will you)\s+)*"
    r"(?P<verb>skip|drop|remove|cancel|scratch|forget|take|no stop at|don'?t stop at|not stopping at)\b(?P<obj>.*)$", re.I)
TAKE_OFF = re.compile(r"^\s+off\b(?!\s+from)|\boff(?:\s+(?:the|my)\s+(?:route|list|trip))?\s*$", re.I)
NOT_A_STOP_OBJECT = re.compile(r"\b(?:trailers?|loads?|freight|cargo|reefer|pallets?|time)\b"
                               r"|\b\d{1,2}(?::\d\d)?\s*(?:am|pm|a\.m\.|p\.m\.|o'?clock)\b|\b(?:noon|midnight)\b", re.I)
# What the model puts in clear_stops' query besides a name (review 2026-11-02: "truck stop", "my stops", "the last
# stop", "straight there" were each "X isn't one of your stops"). All of them:
ALL_STOPS = re.compile(r"^(?:the |all |both |my |of |these |those )*(?:stops?|all|both|everything|them)$"
                       r"|\b(?:all|both|my|the|these|those|no|any) (?:of (?:the |my )?)?stops\b|\bstraight (?:there|through|to)\b",
                       re.I)
STOP_ORDER = re.compile(r"^(?:the )?(?P<ord>first|last|next|second|third)(?: one| stop)?$", re.I)
THAT_STOP = re.compile(r"^(?:that|this|it|the one you (?:just )?added)(?: one| stop)?$", re.I)
STOP_KIND = ((re.compile(r"\b(?:truck|fuel|gas|diesel) stop\b|\btravel (?:center|plaza)\b|^fuel$", re.I), "truck_stop"),
             (re.compile(r"\brest (?:area|stop)\b", re.I), "rest_area"),
             (re.compile(r"\b(?:hotel|motel)\b", re.I), "hotel"),
             (re.compile(r"\b(?:cat )?scale\b", re.I), "cat_scale"),
             (re.compile(r"\btruck wash\b", re.I), "truck_wash"),
             (re.compile(r"\bwalmart\b", re.I), "walmart"))
# "Add another truck stop", "a different one", "one more": a stop besides those on the route.
ANOTHER = re.compile(r"\b(?:another|different|one more|other|else|second|extra)\b", re.I)
ROAD_WORDS = re.compile(r"\b(?:way|road|route|highway|freeway|interstate|detour|grade|pass|toll)s?\b", re.I)
# "The second one", "number two", "the last one": a reference into the list, never something to geocode
# (HERE once turned "the second one" into W 21st Pl, The Dalles, OR, 598 miles out).
LIST_REF = re.compile(
    r"^(?:the |that |this |number |no\.? ?|#)?"
    r"(?P<ord>first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|one|two|three|four|five|1|2|3|4|5|last|closest|nearest)"
    r"(?: one| place| stop| option)?\.?$", re.I)
ORDINALS = {"first": 1, "1st": 1, "one": 1, "1": 1, "second": 2, "2nd": 2, "two": 2, "2": 2, "third": 3, "3rd": 3,
            "three": 3, "3": 3, "fourth": 4, "4th": 4, "four": 4, "4": 4, "fifth": 5, "5th": 5, "five": 5, "5": 5}
# A bare pronoun or deictic the model turned into a destination: nothing to geocode either.
NOT_A_PLACE = re.compile(r"^(?:that|this|it|there|here|that one|this one|the other one|other one)\.?$", re.I)
# "Remember I like Pilot", "keep in mind I hate tolls": the driver's own word for a like or a dislike.
REMEMBER = re.compile(r"\b(?:remember|keep in mind|make a note|note that|don'?t forget)\b", re.I)
# Road preferences said in words: (pattern, how it is remembered, the profile switch it also sets).
ROAD_PREFS = ((re.compile(r"\btolls?\b|\btoll ?roads?\b", re.I), "tolls", "avoid_tolls"),
              (re.compile(r"\bferr(?:y|ies)\b|\btunnels?\b", re.I), "ferries and tunnels", "avoid_ferries_tunnels"),
              (re.compile(r"\bdowntown\b|\bcity driving\b", re.I), "downtown", None),
              (re.compile(r"\bnight driving\b|\bdriving at night\b", re.I), "night driving", None))
# "Nothing else" is the end of the conversation; next to "something else" the model can read it as a pick.
BYE_PHRASE = re.compile(r"^(?:no,? )?(?:nothing else|that'?s (?:all|it)|i'?m (?:good|all set)|all set|no thanks?)[.!]?$", re.I)


def numbered(places: list[Place], current_id: Optional[str], screen: Optional[list[str]] = None) -> list[Place]:
    """The dialog's places in the order the model numbers them: the cards on screen in screen order (ui.option_ids,
    when the app sends them), then the other options in the order the app got them, then the current route. The
    model's list used to start with the current route while the cards do not, and "the second one" took the first
    card (QA 2026-11-02, bug 6)."""
    others = [p for p in places if p.id != current_id]
    by_id = {p.id: p for p in others}
    shown = [by_id[i] for i in dict.fromkeys(screen or []) if i in by_id]
    return shown + [p for p in others if p not in shown] + [p for p in places if p.id == current_id]


def list_reference(query: str, places: list[Place], current_id: Optional[str],
                   screen: Optional[list[str]] = None) -> Optional[int]:
    """1-based index into numbered(places) for "the second one" & co, counted over the places the driver can
    see (the cards on screen when the app names them, else the options; the current route excluded, as on the
    options list); None when the query is not a reference."""
    m = LIST_REF.match((query or "").strip())
    if not m:
        return None
    order = numbered(places, current_id, screen)
    on_screen = [p for p in order if p.id in set(screen or []) and p.id != current_id]
    shown = on_screen or [p for p in places if p.id != current_id] or list(places)
    if not shown:
        return None
    word = m.group("ord").lower()
    if word == "last":
        chosen = shown[-1]
    elif word in ("closest", "nearest"):
        chosen = min(shown, key=lambda p: p.distance_mi if p.distance_mi is not None else 1e9)
    else:
        n = ORDINALS.get(word)
        if not n or n > len(shown):
            return None
        chosen = shown[n - 1]
    return order.index(chosen) + 1


_NAME_TOKEN = re.compile(r"[a-z0-9]+")


def _name_tokens(s: str) -> set[str]:
    """Lowercase words, apostrophes dropped, a plural "s" trimmed: the model said "Roady's Truck Stops"."""
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in _NAME_TOKEN.findall((s or "").lower().replace("'", ""))}


def named_place(query: str, places: list[Place]) -> Optional[Place]:
    """The listed place the driver named ("the 49er", "the Pilot"), or None. Every word of the query has to
    be in the place name, and at least one of them must not be a word of the address (the town): "take me
    to Sacramento" never matches "Sacramento 49er Travel Plaza", "the 49er" does. Geocoding "49er" gave a
    street in Placerville (QA 2026-10-31, H9)."""
    words = _name_tokens(re.sub(r"^\s*(?:the|a|an)\s+", "", query or "", flags=re.I))
    if not words:
        return None
    for p in places:
        name = _name_tokens(p.name)
        if words <= name and words - _name_tokens(p.address or ""):
            return p
    return None


NAME_LIKE = re.compile(r"^(?:the\s+)?[A-Za-z0-9&'.\- ]{2,40}$")


def name_like(query: str) -> bool:
    """"the 49er", "Roady's", "Pilot": a short name without a house number or a street word, the kind of query
    that is a truck stop's name, not an address."""
    q = (query or "").strip()
    if not NAME_LIKE.match(q) or len(q.split()) > 3:
        return False
    if re.match(r"^\d+\s", q) or re.search(r"\b(?:st|street|ave|avenue|rd|road|blvd|hwy|highway|dr|drive|way|exit|mile)\b", q, re.I):
        return False
    return True


GENERIC_NAME_WORDS = {"truck", "stop", "stops", "travel", "center", "centre", "plaza", "inn", "motel", "hotel",
                      "rest", "area", "the", "and", "of", "at", "north", "south", "east", "west", "northbound",
                      "southbound", "eastbound", "westbound", "station", "fuel", "gas", "diesel"}


def named_in_text(text: str, places: list[Place]) -> Optional[Place]:
    """The listed place whose own word the driver said ("stop at the 49er on the way" -> the 49er), or None.
    A word counts when it is the place's, not its town's and not a generic ("truck stop", "travel plaza")."""
    said = _name_tokens(text)
    for p in places:
        own = {w for w in _name_tokens(p.name) - _name_tokens(p.address or "") - GENERIC_NAME_WORDS if len(w) >= 3}
        if own & said:
            return p
    return None


def stop_named(text: str, query: str, stops: list[Place], dest: Optional[Place] = None) -> Optional[Place]:
    """The route's stop the driver named ("skip the Pilot", "drop the Fernley stop"), or None. Only the stops are
    candidates, so the town counts too (named_in_text leaves out words of the address: "Pilot" is a word of
    "100 Pilot Rd"), except the destination's words ("no stops, straight to Reno" keeps no Reno stop); the stop
    with the most words said wins, the first in route order on a tie."""
    said_words = (_name_tokens(text) | _name_tokens(query)) - (_name_tokens(dest.name) if dest else set())
    best, hits = None, 0
    for s in stops:
        own = {w for w in _name_tokens(s.name) - GENERIC_NAME_WORDS if len(w) >= 3}
        n = len(own & said_words)
        if n > hits:
            best, hits = s, n
    return best


def stop_to_drop(text: str, stops: list[Place], dest: Optional[Place] = None) -> Optional[Place]:
    """The route's stop an order in the text leaves out ("skip the Pilot", "cancel the Fernley stop"), or None."""
    for clause in re.split(r"[.!?;,]+|\bthen\b", text or ""):
        m = DROP_ORDER.match(clause.strip())
        if not m:
            continue
        obj = m.group("obj")
        if m.group("verb").lower() == "take" and not TAKE_OFF.search(obj):
            continue
        if NOT_A_STOP_OBJECT.search(obj):
            continue
        stop = stop_named(obj, "", stops, dest)
        if stop:
            return stop
    return None


def reject_or_reference_guard(intent: str, d: dict, text: str, places: list[Place],
                              active: Optional[Place], screen: Optional[list[str]] = None) -> tuple[str, dict]:
    """Rewrites two classifier mistakes before anything acts on them:
    - alternative + a rejection phrase (and no road word) -> pick without an index (= the next place);
    - go + a list reference ("the second one", place_index given) -> pick with that index;
    - go + a bare pronoun -> not_understood instead of a geocode of "that".
    Everything else passes through untouched."""
    current_id = active.id if active else None
    if intent == "pick" and BYE_PHRASE.match(text.strip()):
        logger.info("agent pick %s is a goodbye -> bye", said(text))
        return "bye", {**d, "intent": "bye", "place_index": None}
    if intent == "pick" and places and LIST_REF.match(text.strip()):
        # "the second one" is counted here, not by the model (QA 2026-11-02, bug 6)
        ref = list_reference(text.strip(), places, current_id, screen)
        if ref is not None and ref != d.get("place_index"):
            logger.info("agent pick %s: the model said %s, the list says %s", said(text), d.get("place_index"), ref)
            return "pick", {**d, "place_index": ref}
    if intent == "alternative" and places and REJECT_PLACE.search(text) and not ROAD_WORDS.search(text):
        logger.info("agent alternative %s reads as a rejected place -> pick", said(text))
        return "pick", {**d, "intent": "pick", "place_index": None}
    if intent == "go":
        query = str(d.get("query") or "").strip()
        ref = list_reference(query, places, current_id, screen) if places else None
        if ref is None and places and LIST_REF.match(text.strip()):
            ref = list_reference(text.strip(), places, current_id, screen)
        if ref is not None:
            logger.info("agent go %s is a list reference -> pick %s", said(query or text), ref)
            return "pick", {**d, "intent": "pick", "place_index": ref}
        idx = d.get("place_index")
        if places and isinstance(idx, int) and 1 <= idx <= len(places) and (not query or LIST_REF.match(query) or NOT_A_PLACE.match(query)):
            logger.info("agent go with place_index=%s and no real destination -> pick", idx)
            return "pick", {**d, "intent": "pick", "place_index": idx}
        if LIST_REF.match(query) or NOT_A_PLACE.match(query):
            logger.info("agent go %r is not a place; nothing to pick from", query)
            return ("pick", {**d, "intent": "pick", "place_index": None}) if places else ("not_understood", d)
    return intent, d


def pick_place(d: dict, places: list[Place], current_id: Optional[str] = None,
               screen: Optional[list[str]] = None) -> Optional[Place]:
    """Resolves the model's 1-based place_index into a place found earlier, numbered as the model saw them."""
    try:
        i = int(d.get("place_index"))
    except (TypeError, ValueError):
        return None
    order = numbered(places, current_id, screen)
    return order[i - 1] if 1 <= i <= len(order) else None


# "skip / drop / cancel ..." at the start of the driver's words, for reading the stop meant from what follows.
ORDER_VERB = re.compile(r"^\s*(?:please\s+|let'?s\s+|just\s+|go ahead and\s+)?(?:skip|drop|cancel|remove|ditch|lose)\s+", re.I)


# "Where am I?": the town and road from a reverse geocode, never the model (it read out "38.58160, -121.49440":
# synthetic voice run 2026-11-08).
WHERE_AM_I = re.compile(r"^\W*(?:hey,? )?(?:where am i|where are we|what (?:town|city) (?:is this|am i in|are we in)|"
                        r"what(?:'s| is) my (?:location|position)|где я|где мы|де я|де ми)\b(?! (?:going|headed|heading|parking|parked|stopping|sleeping))",
                        re.I)


# "Take the cheapest route", "is there a cheaper way", "the fastest way there": a choice among the ways to the same
# destination (with ROAD_WORDS and no fuel word; "cheapest diesel on my route" is a stop).
ROUTE_CHEAPEST = re.compile(r"\bcheap(?:est|er)\b|\bsave (?:money|gas|fuel)\b|\bless money\b", re.I)
ROUTE_FASTEST = re.compile(r"\b(?:fastest|quickest|faster|quicker|shortest time)\b", re.I)

# "How much is diesel there?", "what's fuel cost at the Pilot": the price at one place, not which is cheapest.
# The model filed it under reviews ("No driver reviews there yet": synthetic voice run 2026-11-08).
PRICE_AT = re.compile(
    r"\b(?:price|prices|cost|costs|going for|per gallon|a gallon)\b"
    r"|\bhow much (?:is|are|does|'s|for)\b"
    r"|\bwhat(?:'s| is| are) (?:the )?(?:diesel|fuel|gas)\b[^.?!]*\b(?:there|here|at)\b", re.I)
