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
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("truckbuddy.i18n")

LANGS = ("en", "ru", "uk")
CATALOG_DIR = Path(__file__).resolve().parent.parent / "i18n"
_lang: contextvars.ContextVar[str] = contextvars.ContextVar("tb_lang", default="en")
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

def plural_ru(n: float, one: str, few: str, many: str) -> str:
    if n != int(n):
        return few  # 2,5 мили
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


def _number(value: str) -> Optional[float]:
    m = _NUM.search(value or "")
    return float(m.group(0).replace(",", ".")) if m else None


# ---- translation -----------------------------------------------------------------------------------------------

def _apply(filt: Optional[str], value: str, code: str, words: dict) -> str:
    if not filt:
        return value
    kind, _, arg = filt.partition(":")
    if kind == "plural":
        forms = arg.split(",")
        n = _number(value)
        if n is None or len(forms) != 3:
            return value
        return f"{value} {plural_ru(n, *forms)}"
    if kind == "and":
        return value.replace(" and ", " и ")
    if kind == "time":
        return time_ru(value, case=arg or "nom")
    if kind in ("tr", "part"):
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
        return _apply(pm.group(2), vals.get(pm.group(1)) or "", code, words)

    return _PH.sub(sub, t.target)


def _match(text: str, code: str, parts: bool = False) -> Optional[str]:
    tpls, words = catalog(code)
    for t in tpls:
        if t.part and not parts:
            continue
        m = t.rx.fullmatch(text)
        if m:
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
                   "name (I-80, CA-99) and place name exactly as written. Plain spoken text, one line, nothing else.")
_LANG_NAMES = {"ru": "Russian", "uk": "Ukrainian"}


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

    done: dict[str, str] = {}
    for s in misses:
        try:
            got = (await llm.complete(FALLBACK_PROMPT.format(language=_LANG_NAMES.get(code, code)), s)).strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("i18n fallback failed: %s", e)
            continue
        if got and numbers_kept(s, got):
            done[s] = got
        elif got:
            logger.warning("i18n fallback changed numbers, kept English: %r -> %r", s, got)
    return translate(text, code, fallback=lambda s, c: done.get(s))


# ---- whole responses -------------------------------------------------------------------------------------------

def localize_route(route):
    """Turn instructions and route notices of a RouteInfo (and its alternatives) in this request's language, by the
    catalog only. HERE answers in English on purpose: services/approach.py reads the street of the way in from the
    English instruction ("... onto El Centro Rd"); the words are swapped only on the way out."""
    if route is None or lang() == "en":
        return route
    # A copy: the dialog session keeps this route, and approach reads its English instructions again later.
    route = route.model_copy(deep=True) if hasattr(route, "model_copy") else route
    for m in getattr(route, "maneuvers", None) or []:
        m.instruction = translate(m.instruction, fallback=None)
    if getattr(route, "notices", None):
        route.notices = [translate(n, fallback=None) for n in route.notices]
    if getattr(route, "alternatives", None):
        route.alternatives = [localize_route(alt) for alt in route.alternatives]
    return route


async def localize_parking(check):
    """A ParkingCheck's card text (verdict label, reason, entry and parking tips) in this request's language."""
    if check is None or lang() == "en":
        return check
    for f in ("label", "reason", "entry_tip", "park_tip"):
        v = getattr(check, f, None)
        if v:
            setattr(check, f, await localize(v))
    return check


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
