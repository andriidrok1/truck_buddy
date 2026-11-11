"""The copilot's language for this request, and the translation of what it says.

Nearly every spoken line is built in code from English templates (the model writes only chat and bye lines), so
the catalog maps those templates, not free text: i18n/<lang>.json holds {"en": template, "<lang>": template}
pairs with the same {placeholders}. A finished English reply is matched against the templates (whole reply, else
runs of sentences); the captured values move over unchanged, so numbers, miles, times and place names can never
be mistranslated. A Russian template can shape a value with a filter:

  {mi|plural:миля,мили,миль}  the number with the noun in its form (1 миля, 2 мили, 5 миль)
  {time|time}                 "2 h 52 min" -> "2 часа 52 минуты"
  {what|acc}                  a category or phrase from the "words" table in the case asked (nom, acc, gen, prep)
  {x|tr}                      the value translated itself (any template or word: a nested sentence)
  {x|part}                    the value is a fragment ("part" template) or empty; it also bounds the match, so in
                              "{name}{off}" the name stops where ", right off ..." starts
  {at|clock}                  "3:30 pm" -> "15:30", "tomorrow 8:58 am" -> "завтра в 8:58"

Entries with "part": true are fragments (", right off {street}", " via {roads}") used only inside {x|part}; they never
match a whole sentence, so a short generic fragment cannot swallow a line it was not written for.

A sentence no template matches is translated by the model only when the result keeps every number of the source;
otherwise it stays English. Misses are logged (`i18n miss`) so the catalog can grow from real traffic.

The language is a context variable set once per request (set_lang); everything downstream reads lang().
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("truckbuddy.i18n")

LANGS = ("en", "ru", "uk")
CATALOG_DIR = Path(__file__).resolve().parent.parent / "i18n"
_lang: contextvars.ContextVar[str] = contextvars.ContextVar("tb_lang", default="en")
_fallback: contextvars.ContextVar[Optional[Callable[[str, str], Optional[str]]]] = contextvars.ContextVar(
    "tb_i18n_fallback", default=None)  # translate()'s fallback, for the {x|mt} values inside a matched template
_PH = re.compile(r"\{(\w+)(?:\|([^{}]*))?\}")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def norm(lang: Optional[str]) -> str:
    code = (lang or "en").strip().lower().split("-")[0].split("_")[0]
    return code if code in LANGS else "en"


def set_lang(lang: Optional[str]) -> str:
    code = norm(lang)
    _lang.set(code)
    return code


def lang() -> str:
    return _lang.get()


def use_lang(explicit: Optional[str]) -> str:
    """An endpoint's language: a value sent in the request itself wins; without one the X-Lang header's (set by
    LangMiddleware) stands. The app sends only the header, so resetting from an empty field made every answer
    English (review 2026-11-05)."""
    if explicit:
        return set_lang(explicit)
    return lang()


# ---- catalog ---------------------------------------------------------------------------------------------------

# A captured value never runs over a sentence end: "Okay, no stop at {name}." must not swallow "Pilot. New total ...".
_VALUE = r"(?:(?![.!?]\s).)*?"


class Template:
    __slots__ = ("en", "target", "rx", "fixed", "part")

    def __init__(self, en: str, target: str, part: bool = False):
        self.en, self.target, self.part = en, target, part
        self.fixed = len(re.sub(_PH, "", en))  # literal length: the more literal text, the surer the match
        self.compile(None)

    def tr_names(self) -> set[str]:
        return {m.group(1) for m in _PH.finditer(self.target) if (m.group(2) or "") == "part"}

    def compile(self, fragment: Optional[str]) -> None:
        """A placeholder the target translates as a fragment ({x|part}) may hold only a known fragment or nothing;
        so in "{name}{off}" the name stops where ", right off ..." starts."""
        tr = self.tr_names() if fragment else set()
        parts, last, names = [], 0, []
        for m in _PH.finditer(self.en):
            parts.append(re.escape(self.en[last:m.start()]))
            name = m.group(1)
            if name in names:
                parts.append(f"(?P={name})")
            elif name in tr:
                parts.append(f"(?P<{name}>(?:{fragment})?)")
            else:
                parts.append(f"(?P<{name}>{_VALUE})")
            names.append(name)
            last = m.end()
        parts.append(re.escape(self.en[last:]))
        self.rx = re.compile("".join(parts), re.S)


@lru_cache(maxsize=8)
def catalog(code: str) -> tuple[list[Template], dict]:
    """(templates sorted most specific first, words table) for a language; empty for English or a missing file."""
    path = CATALOG_DIR / f"{code}.json"
    if code == "en" or not path.exists():
        return [], {}
    data = json.loads(path.read_text())
    tpls = [Template(e["en"], e[code], bool(e.get("part"))) for e in data.get("templates", [])
            if e.get("en") and e.get(code) is not None]
    tpls.sort(key=lambda t: -t.fixed)
    # Fragments as one anonymous alternation, for the {x|part} slots of the other templates.
    # Only fragments that open with literal text (", right off {street}") bound a slot: one that opens with a
    # placeholder ("{head} in {town}") would fit almost anything ("3:30 pm, in effect till ...").
    frags = [re.sub(r"\(\?P<\w+>", "(?:", re.sub(r"\(\?P=\w+\)", ".*?", t.rx.pattern)) for t in tpls
             if t.part and not t.en.startswith("{")]
    if frags:
        alt = "|".join(f"(?:{f})" for f in frags)
        for t in tpls:
            if t.tr_names():
                t.compile(alt)
    return tpls, data.get("words", {})


# ---- Russian forms ---------------------------------------------------------------------------------------------

def plural_ru(n: float, one: str, few: str, many: str, frac: Optional[str] = None) -> str:
    """The noun after n. A fraction takes the genitive singular ("3,2 мили", "около 0,5 мили"): `frac` when the
    template gives it (a case whose forms don't hold it: "в {mi|plural:миле,милях,милях,мили}"), else `few` of a
    nominative set (мили, фута) or `one` of a set whose few and many agree (the genitive мили,миль,миль)."""
    if n != int(n):
        return frac or (one if few == many else few)
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


_TIME = re.compile(r"^\s*(?:(\d+)\s*(?:h|hours?))?\s*(?:(\d+)\s*(?:min|minutes?))?\s*$")


def time_ru(value: str, case: str = "nom") -> str:
    """'2 h 52 min' / '4 hours 20 minutes' / '45 min' / '3 h' -> '2 часа 52 минуты' / '4 часа 20 минут' / ...;
    anything else unchanged."""
    m = _TIME.match(value or "")
    if not m or not (m.group(1) or m.group(2)):
        return value
    out = []
    hours = ("часа", "часов", "часов") if case == "gen" else ("час", "часа", "часов")
    mins = ("минуты", "минут", "минут") if case == "gen" else ("минута", "минуты", "минут")
    if m.group(1):
        h = int(m.group(1))
        out.append(f"{h} {plural_ru(h, *hours)}")
    if m.group(2):
        mi = int(m.group(2))
        out.append(f"{mi} {plural_ru(mi, *mins)}")
    return " ".join(out)


_DAYS_RU = {"today": "сегодня", "tonight": "сегодня вечером", "tomorrow": "завтра", "monday": "в понедельник",
            "tuesday": "во вторник", "wednesday": "в среду", "thursday": "в четверг", "friday": "в пятницу",
            "saturday": "в субботу", "sunday": "в воскресенье"}
_CLOCK = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?", re.I)


def clock_ru(value: str) -> str:
    """'3:30 pm' -> '15:30', '8 am' -> '8:00', 'tomorrow 8:58 am' / 'tomorrow at 8:58 am' -> 'завтра в 8:58',
    'at 3 pm' -> 'в 15:00', 'Thursday 6 am' -> 'в четверг в 6:00'."""
    def hhmm(m: re.Match) -> str:
        h, mm, ap = int(m.group(1)), m.group(2) or "00", m.group(3).lower()
        h = (h % 12) + (12 if ap == "p" else 0)
        return f"{h}:{mm}"

    v = (value or "").strip()
    day = ""
    first = v.split(" ", 1)[0].lower()
    if first in _DAYS_RU and " " in v:
        day, v = _DAYS_RU[first] + " ", v.split(" ", 1)[1]
    if v.lower().startswith("at "):
        v = v[3:]
    out = _CLOCK.sub(hhmm, v)
    if out == v and not day:
        return value
    return f"{day}в {out}" if day or (value or "").strip().lower().startswith("at ") else out


_GROUPED = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?!\d|,\d)")  # 200,000 / $1,234 (not 38.58160, -121.4)


def _number(value: str) -> Optional[float]:
    v = _GROUPED.sub(lambda m: m.group(0).replace(",", ""), value or "")  # "81,000" is 81000, not 81.0
    m = _NUM.search(v)
    return float(m.group(0).replace(",", ".")) if m else None


_DECIMAL = re.compile(r"(?<![\w.,])(\d+)\.(\d+)(?![\w.]|,\d)")  # 3.2, $3.45, 4.7 (not R22.5, 1.2.3)


def group_ru(value: str) -> str:
    """A number as Russian writes it: '200,000' -> '200 000' (no-break space; a comma there reads as a decimal point)
    and '3.2' -> '3,2'."""
    out = _GROUPED.sub(lambda m: m.group(0).replace(",", "\u00a0"), value or "")
    return _DECIMAL.sub(r"\1,\2", out)


# ---- translation -----------------------------------------------------------------------------------------------

def _apply(filt: Optional[str], value: str, code: str, words: dict) -> str:
    if not filt:
        return group_ru(value)
    kind, _, arg = filt.partition(":")
    if kind == "plural":
        forms = arg.split(",")
        n = _number(value)
        if n is None or len(forms) not in (3, 4):
            return group_ru(value)
        return f"{group_ru(value)} {plural_ru(n, *forms)}"
    if kind == "and":
        return value.replace(" and ", " и ")
    if kind == "time":
        return time_ru(value, case=arg or "nom")
    if kind in ("tr", "part", "mt"):
        if not value.strip():
            return value
        # Fragments glued one after another (", about 1 h 46 min longer, and it misses the High Wind Warning",
        # "you like Pilot, you avoid toll roads"): each ", ..." piece on its own when every piece is known; this
        # goes first, or a loose fragment ("you like {x}") would take the whole list as its {x}.
        pieces = [p for p in re.split(r"(?=, )", value) if p]
        if len(pieces) > 1:
            done = [_match(p, code, parts=True) for p in pieces]
            if all(d is not None for d in done):
                return "".join(done)
        got = _match(value, code, parts=True)
        if got is not None:
            return got
        if kind == "mt":
            # Text the vision or review model wrote ("To get in: {tip|mt}."): the enclosing translate()'s fallback
            # sends it to the model like a whole sentence, or it stayed English inside a Russian line (QA
            # 2026-11-05). Only these values: {x|tr} holds place names ("Reno"), which must never cost a call.
            return translate(value, code, fallback=_fallback.get())
        return translate(value, code, fallback=None)
    if kind == "clock":
        return clock_ru(value)
    entry = words.get(value.strip().lower())
    if isinstance(entry, dict):
        return entry.get(kind) or entry.get("nom") or value
    if isinstance(entry, str):
        return entry
    return value


def _fill(t: Template, m: re.Match, code: str, words: dict) -> str:
    vals = m.groupdict()

    def sub(pm: re.Match) -> str:
        out = _apply(pm.group(2), vals.get(pm.group(1)) or "", code, words)
        if pm.group(2) == "mt" and t.target[pm.end():pm.end() + 1] in (".", "!", "?"):
            out = out.rstrip(" .")  # the model's own period before the template's: "въезд.." (review of #126)
        return out

    return _PH.sub(sub, t.target)


_VALUE_FILTERS = ("plural", "and", "time", "tr", "part", "mt", "clock")  # every other filter is a form from the words table


def _words_known(t: Template, m: re.Match, words: dict) -> bool:
    """A placeholder the target takes from the words table ({event|nom}, {dir|at}) holds a word that is there.
    Otherwise "{event} near {area}." took a vision tip, "Park along the east side near existing trailers.", and
    said "Park along the east side в районе existing trailers." (QA 2026-11-05)."""
    vals = m.groupdict()
    for pm in _PH.finditer(t.target):
        kind = (pm.group(2) or "").partition(":")[0]
        if kind and kind not in _VALUE_FILTERS and (vals.get(pm.group(1)) or "").strip().lower() not in words:
            return False
    return True


def _match(text: str, code: str, parts: bool = False) -> Optional[str]:
    tpls, words = catalog(code)
    for t in tpls:
        if t.part and not parts:
            continue
        m = t.rx.fullmatch(text)
        if m and _words_known(t, m, words):
            return _fill(t, m, code, words)
    entry = words.get(text.strip().lower())
    if isinstance(entry, dict):
        return entry.get("nom")
    if isinstance(entry, str):
        return entry
    return None


_SENT = re.compile(r"(?<=[.!?])\s+")


def translate(text: str, code: Optional[str] = None, fallback: Optional[Callable[[str, str], Optional[str]]] = None) -> str:
    """The text in `code` (default: this request's language). Whole text first, then the longest runs of sentences
    that match a template, left to right; a run nothing matches goes to `fallback` (number-checked) or stays."""
    code = norm(code or lang())
    if code == "en" or not text or not text.strip():
        return text
    if _CYRILLIC.search(text) and not _LATIN_WORDS.search(_strip_names(text)):
        return text  # already in the language (a model-written chat line)
    token = _fallback.set(fallback)
    try:
        return _translate(text, code, fallback)
    finally:
        _fallback.reset(token)


def _translate(text: str, code: str, fallback: Optional[Callable[[str, str], Optional[str]]]) -> str:
    whole = _match(text.strip(), code)
    if whole is not None:
        return _cap(whole)
    sents = [s for s in _SENT.split(text.strip()) if s]
    out, i = [], 0
    while i < len(sents):
        for j in range(len(sents), i, -1):
            got = _match(" ".join(sents[i:j]), code)
            if got is not None:
                out.append(_cap(got))
                i = j
                break
        else:
            out.append(_miss(sents[i], code, fallback))
            i += 1
    return " ".join(x for x in out if x)


def _cap(s: str) -> str:
    """A sentence starts with a capital, also when it starts with a translated word ("предупреждение о ...")."""
    return s[:1].upper() + s[1:] if s[:1].isalpha() and s[:1].islower() else s


_CYRILLIC = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]")
_LATIN_WORDS = re.compile(r"\b[a-z]{4,}\b")


def _strip_names(text: str) -> str:
    return re.sub(r"\b[A-Z][\w'&.-]*(?:\s+[A-Z][\w'&.-]*)*", "", text)  # capitalised names stay Latin


def numbers_kept(src: str, dst: str) -> bool:
    return sorted(_NUM.findall(src)) == sorted(_NUM.findall(dst))


def _miss(sentence: str, code: str, fallback: Optional[Callable[[str, str], Optional[str]]]) -> str:
    logger.info("i18n miss %s: %s", code, sentence[:200])
    if fallback is None:
        return sentence
    try:
        got = fallback(sentence, code)
    except Exception as e:  # noqa: BLE001 - English beats silence
        logger.warning("i18n fallback failed: %s", e)
        return sentence
    if got and numbers_kept(sentence, got):
        return got.strip()
    if got:
        logger.warning("i18n fallback changed numbers, kept English: %r -> %r", sentence, got)
    return sentence


FALLBACK_PROMPT = ("Translate this line a truck copilot says aloud into {language}. Keep every number, unit, road "
                   "name (I-80, CA-99) and place name exactly as written. Plain spoken text, one line, nothing else.{style}")
# The words the catalog uses, so a model-translated line sits next to a templated one without a change of voice:
# a parking card said «Используйте юго-западный подъезд» next to «Паркуйся в ряду для грузовиков» (QA 2026-11-09).
_STYLE = {
    "ru": (" Talk to the driver as a friend: informal «ты», never «вы» («используй», «паркуйся», «заезжай»). "
           "A truck, rig or semi is «трак» (never «грузовик» or «фура»), a trailer is «трейлер» (never «полуприцеп»), "
           "a truck stop is «трак-стоп»."),
    "uk": (" Talk to the driver as a friend: informal «ти», never «ви». A truck, rig or semi is «трак» "
           "(never «вантажівка» or «фура»), a trailer is «трейлер» (never «напівпричіп»)."),
}
# Part of the model cache key: a new prompt must not serve the translations the old one made.
FALLBACK_VERSION = 2
_LANG_NAMES = {"ru": "Russian", "uk": "Ukrainian"}


def fallback_prompt(code: str) -> str:
    return FALLBACK_PROMPT.format(language=_LANG_NAMES.get(code, code), style=_STYLE.get(code, ""))


async def localize(text: str, code: Optional[str] = None) -> str:
    """translate() with the model as the fallback for lines the catalog misses (numbers checked)."""
    code = norm(code or lang())
    if code == "en" or not text or not text.strip():
        return text
    misses: list[str] = []
    first = translate(text, code, fallback=lambda s, c: misses.append(s) or None)
    if not misses:
        return first
    from services import llm  # lazy: llm is heavy and tests stub it

    import asyncio

    async def one(s: str) -> tuple[str, Optional[str]]:
        try:
            got = (await llm.complete(fallback_prompt(code), s)).strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("i18n fallback failed: %s", e)
            return s, None
        if got and numbers_kept(s, got):
            return s, got
        if got:
            logger.warning("i18n fallback changed numbers, kept English: %r -> %r", s, got)
        return s, None

    async def cached(s: str) -> tuple[str, Optional[str]]:
        key = (code, FALLBACK_VERSION, s.rstrip(" ."))  # "Park along ..." on the card and "Park along ...." said
        if key in _model_cache:
            _model_cache.move_to_end(key)
            return s, _period_like(s, _model_cache[key])
        task = _inflight.get(key)
        if task is None:  # the card fields and the reply ask for the same tip at once: one call
            task = asyncio.ensure_future(one(key[2]))
            _inflight[key] = task
            task.add_done_callback(lambda _t: _inflight.pop(key, None))
        _src, got = await asyncio.shield(task)
        if got:
            _model_cache[key] = got
            _model_cache.move_to_end(key)
            while len(_model_cache) > MODEL_CACHE_MAX:
                _model_cache.popitem(last=False)
        return s, _period_like(s, got) if got else None

    # All misses at once: one after another, three unknown lines added three model round trips to the answer.
    done = {s: got for s, got in await asyncio.gather(*(cached(s) for s in dict.fromkeys(misses))) if got}
    return translate(text, code, fallback=lambda s, c: done.get(s))


# Model translations by (language, English text): a place's vision tips are the same on every request, so the
# card and the spoken answer pay the model once per text, not once per request. Bounded, oldest out.
MODEL_CACHE_MAX = 2000
_model_cache: "OrderedDict[tuple[str, int, str], str]" = OrderedDict()
_inflight: dict = {}


def _period_like(src: str, got: str) -> str:
    """The translation ends with a period exactly when the English did (the cache key has none)."""
    got = got.rstrip(" .")
    return got + "." if src.rstrip().endswith(".") else got


# ---- whole responses -------------------------------------------------------------------------------------------

def _whole_or_english(text: str) -> str:
    """The catalog's translation when every sentence of the text has a template, else the English as it is: a
    half-translated turn ("Take middle ramp onto I-5 N toward Redding" with "Прибытие:" glued in) reads worse than
    the router's own words."""
    misses: list[str] = []
    out = translate(text, fallback=lambda s, c: misses.append(s) or None)
    return text if misses else out


def localize_route(route):
    """Turn instructions, route notices and what lies ahead (incidents, NWS weather events, chain controls) of a
    RouteInfo (and its alternatives) in this request's language, by the catalog only. HERE answers in English on
    purpose: services/approach.py reads the street of the way in from the English instruction ("... onto El Centro
    Rd"); the words are swapped only on the way out. A line the catalog does not fully cover stays English whole."""
    if route is None or lang() == "en":
        return route
    # A copy: the dialog session keeps this route, and approach reads its English instructions again later.
    route = route.model_copy(deep=True) if hasattr(route, "model_copy") else route
    for m in getattr(route, "maneuvers", None) or []:
        m.instruction = _whole_or_english(m.instruction)
    if getattr(route, "notices", None):
        route.notices = [_whole_or_english(n) for n in route.notices]
    for inc in getattr(route, "incidents", None) or []:
        inc.type = _whole_or_english(inc.type)
        inc.description = _whole_or_english(inc.description)
    for field in ("weather", "weather_later"):
        for a in getattr(route, field, None) or []:
            a.event = _whole_or_english(a.event)
    for c in getattr(route, "chain_controls", None) or []:
        if c.description:
            c.description = _whole_or_english(c.description)
    if getattr(route, "alternatives", None):
        route.alternatives = [localize_route(alt) for alt in route.alternatives]
    return route


def localize_call_card(card):
    """The breakdown "What to tell them" card in this request's language, on a copy: the questions, the tire and
    the truck line. The problem is the driver's own words; share_text goes to the shop, so it stays English."""
    if card is None or lang() == "en":
        return card
    update = {"questions": [_whole_or_english(q) for q in card.questions], "truck": _whole_or_english(card.truck)}
    if card.tire:
        update["tire"] = _whole_or_english(card.tire)
    return card.model_copy(update=update)


# ---- the driver's own spelling of a place ----------------------------------------------------------------------
# "Маршрут до Рино" came back as "Маршрут до Reno готов" (QA 2026-11-09, bug 8): the geocoder names places in
# Latin letters. When the driver wrote the name in Cyrillic, the reply uses the driver's spelling. Only a
# whole run of capitalised Latin words is swapped, and only when it sounds the same as a word the driver wrote, so
# "Pilot Travel Center" stays as it is unless the driver wrote all of it.

_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґ",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s",
                      "t", "u", "f", "h", "ts", "ch", "sh", "sh", "", "y", "", "e", "iu", "ia", "i", "i", "e", "g"]))
_CYR_WORD = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]+(?:-[А-Яа-яЁёІіЇїЄєҐґ]+)*")
_LATIN_NAME = re.compile(r"(?<![\w'’-])[A-Z][a-z]+(?:[ -][A-Z][a-z]+)*(?![\w'’-])")
_CASE_ENDINGS = ("ом", "ою", "ой", "а", "у", "е", "ы")  # "до Далласа" -> "Даллас"


def _sound(latin: str) -> str:
    """A rough sound key shared by an English name and its Russian spelling: Reno/Рино, Sacramento/Сакраменто,
    Los Angeles/Лос-Анджелес, Stockton/Стоктон."""
    s = re.sub(r"[^a-z]", "", latin.lower())
    for a, b in (("sch", "sh"), ("tch", "ch"), ("ck", "k"), ("ph", "f"), ("qu", "kv"), ("x", "ks"), ("w", "v"),
                 ("j", "dzh")):
        s = s.replace(a, b)
    s = re.sub(r"g(?=[eiy])", "dzh", s)
    s = re.sub(r"c(?=[eiy])", "s", s).replace("c", "k").replace("ts", "s").replace("kh", "h")
    s = re.sub(r"[eiy]", "i", re.sub(r"[ao]", "a", s))
    return re.sub(r"(.)\1+", r"\1", s)


def _cyr_sound(word: str) -> str:
    return _sound("".join(_TRANSLIT.get(ch, ch) for ch in word.lower()))


def _title(word: str) -> str:
    return "-".join(p[:1].upper() + p[1:] for p in word.split("-"))


def driver_names(text: str, said: Optional[str]) -> str:
    """`text` with each place name the driver wrote in Cyrillic spelled the driver's way."""
    if not text or not said or not _CYRILLIC.search(said):
        return text
    spelled: dict[str, str] = {}
    for w in _CYR_WORD.findall(said):
        forms = [w] + [w[:-len(e)] for e in _CASE_ENDINGS if w.lower().endswith(e) and len(w) - len(e) >= 4]
        for f in forms:
            key = _cyr_sound(f)
            if len(key) >= 3:
                spelled.setdefault(key, _title(f))
    if not spelled:
        return text
    return _LATIN_NAME.sub(lambda m: spelled.get(_sound(m.group(0)), m.group(0)), text)


async def localize_reply(text: str, said: Optional[str] = None, code: Optional[str] = None) -> str:
    """A reply as the driver sees and hears it: localize() plus the driver's own spelling of the places."""
    code = norm(code or lang())
    if code == "en" or not text:
        return text
    return driver_names(await localize(text, code), said)


async def localize_parking(check):
    """A ParkingCheck's card text (verdict label, reason, entry and parking tips) in this request's language, on a
    copy. The check is usually the cached verdict itself (services/parking.py, approach.best): changed in place,
    a Russian request left Russian text in the cache, the next English request showed it and a re-check could
    save it to the table (QA 2026-11-05). The cache stays English; every response is translated on its own."""
    if check is None or lang() == "en":
        return check
    import asyncio

    fields = {f: v for f in ("label", "reason", "entry_tip", "park_tip") if (v := getattr(check, f, None))}
    tasks = {f: asyncio.ensure_future(_localize_clauses(v)) for f, v in fields.items()}
    _done, late = await asyncio.wait(tasks.values(), timeout=CARD_BUDGET_S)
    update = {}
    for f, task in tasks.items():
        if task in late:
            # Over the budget: the catalog's part now, the model's English; the call goes on and fills the cache.
            pieces = fields[f].split("; ")
            update[f] = _join_clauses(pieces, [translate(p, fallback=None) for p in pieces])
        else:
            update[f] = task.result()
    if late:
        logger.info("parking card translation over %.1f s: %s left to the catalog", CARD_BUDGET_S,
                    ", ".join(f for f, t in tasks.items() if t in late))
    return check.model_copy(update=update)


CARD_BUDGET_S = 4.0  # the whole card; /route waits for it


async def _localize_clauses(text: str) -> str:
    """A card line in clauses joined by "; " (the vision reason plus our "there's a lot with trailers to the
    north"): each clause on its own, so the catalog's clause is not handed to the model inside a model-written one."""
    import asyncio

    pieces = text.split("; ")
    return _join_clauses(pieces, await asyncio.gather(*(localize(p) for p in pieces)))


def _join_clauses(pieces: list[str], done: list[str]) -> str:
    out = []
    for i, (p, got) in enumerate(zip(pieces, done)):
        if i and p[:1].islower() and got[:1].isupper():
            got = got[:1].lower() + got[1:]  # mid-line after "; " stays lower case, as in English
        out.append(got)
    return "; ".join(out)


class LangMiddleware:
    """Pure ASGI: the X-Lang header sets the request's language before the endpoint runs (in the same context), so
    /route, /reroute, /parking and the rest answer in it without a field on every request model."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            raw = dict(scope.get("headers") or []).get(b"x-lang")
            set_lang(raw.decode("latin-1") if raw else None)
        await self.app(scope, receive, send)
