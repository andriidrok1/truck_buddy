"""Text-for-speech layer: turns on-screen copy into something that sounds natural when spoken.

Only the TTS input goes through spoken(); captions on screen stay as written.
"""
import re

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def words(n: int) -> str:
    """0..9999 as English words."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + (f"-{_ONES[o]}" if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return f"{_ONES[h]} hundred" + (f" {words(r)}" if r else "")
    if n < 10000:
        th, r = divmod(n, 1000)
        return f"{_ONES[th]} thousand" + (f" {words(r)}" if r else "")
    return str(n)


def _miles(m: re.Match) -> str:
    about, raw = m.group(1) or "", m.group(2).replace(",", "")
    v = float(raw)
    if v < 0.4:
        return "less than half a mile"
    if v < 10:
        half = max(0.5, round(v * 2) / 2)
        whole, frac = int(half), half - int(half)
        if whole == 0:
            text = "half a mile"
        else:
            text = words(whole) + (" and a half" if frac else "") + (" mile" if half == 1 else " miles")
        exact = abs(half - v) < 1e-9
    else:
        r = round(v)
        text, exact = f"{words(r)} miles", abs(r - v) < 1e-9
    return f"{about or ('' if exact else 'about ')}{text}"


def _minutes(m: re.Match) -> str:
    about, v = m.group(1) or "", int(m.group(2))
    r = max(5, int(5 * round(v / 5)))
    if r == 60:
        return "about an hour"
    return f"{about or ('' if r == v else 'about ')}{words(r)} minutes"


def _hours(m: re.Match) -> str:
    h, mins = int(m.group(1)), m.group(2)
    out = "one hour" if h == 1 else f"{words(h)} hours"
    m = int(5 * round(int(mins) / 5)) if mins else 0
    if m >= 60:
        h, m = h + 1, 0
        out = "one hour" if h == 1 else f"{words(h)} hours"
    if m:
        out += f" and {words(m)} minutes"
    return out


_RULES = [
    (re.compile(r"\bI-(\d+)\b"), r"Interstate \1"),
    (re.compile(r"\bUS-(\d+)\b"), r"U.S. \1"),
    (re.compile(r"\b(?:SR|CA)-(\d+)\b"), r"State Route \1"),
    (re.compile(r"\bHwy\b\.?"), "Highway"),
    (re.compile(r"(?<![.\w])W\.(?=\s)"), "West"),
    (re.compile(r"(?<![.\w])N\.(?=\s)"), "North"),
    (re.compile(r"(?<![.\w])S\.(?=\s)"), "South"),
    (re.compile(r"(?<![.\w])E\.(?=\s)"), "East"),
    # After a street name ("Main St. Exit 12", "3rd St") it's a suffix; before a name
    # ("near St. Louis", "Dr. Martin...") it's Saint / Doctor; otherwise street suffixes.
    (re.compile(r"(\b[A-Z0-9][\w']*\s+)St\b"), r"\1Street"),
    (re.compile(r"(\b[A-Z0-9][\w']*\s+)Dr\b"), r"\1Drive"),
    (re.compile(r"\bSt\.?\s+(?=[A-Z][a-z])(?!Next\b|Then\b|Turn\b|The\b)"), "Saint "),
    (re.compile(r"\bDr\.?\s+(?=[A-Z][a-z])(?!Next\b|Then\b|Turn\b|The\b)"), "Doctor "),
    (re.compile(r"\bAve\b"), "Avenue"),
    (re.compile(r"\bSt\b(?=[\s,.]|$)"), "Street"),
    (re.compile(r"\bBlvd\b"), "Boulevard"),
    (re.compile(r"\bRd\b"), "Road"),
    (re.compile(r"\bPkwy\b"), "Parkway"),
    (re.compile(r"\bDr\b(?=[\s,.]|$)"), "Drive"),
    (re.compile(r"\b24/7\b"), "twenty-four seven"),
    (re.compile(r"\bDEF\b"), "D-E-F"),
    (re.compile(r"\s*&\s*"), " and "),
]
_HOURS = re.compile(r"\b(\d+)\s*h(?:ours?|rs?)?(?:\s+(\d+)\s*min(?:utes?)?)?\b")
_MINUTES = re.compile(r"\b(about\s+|around\s+)?(\d+)\s*min(?:utes?|s)?\b", re.I)
_MILES = re.compile(r"\b(about\s+|around\s+)?(\d[\d,]*(?:\.\d+)?)\s*(?:mi|miles?)\b(?!-)", re.I)


# Sentence boundary for speech: ". " before a capital, but not after St./Dr./U.S. abbreviations.
SENTENCE_END = re.compile(r"(?<!\bSt\.)(?<!\bDr\.)(?<!U\.S\.)(?<=[.!?])\s+(?=[A-Z])")
# Clause boundary: a comma or dash followed by a space. Used to hand the voice its first clip sooner.
CLAUSE_END = re.compile(r"(?<=,)\s+|\s+[—–-]\s+")
MIN_CLAUSE_WORDS = 4  # the first clip must carry some meaning ("Alright, got one" is fine; "Okay," alone is not)
MIN_REST_WORDS = 3  # and the remainder must be worth a second clip


def split_reply(text: str, clause: bool = True) -> tuple[str, str]:
    """(first clip, rest) for TTS. With `clause`, the first clip ends at the first comma or dash once it
    has MIN_CLAUSE_WORDS words and leaves MIN_REST_WORDS behind; otherwise at the first sentence end.
    The rest may be empty."""
    t = " ".join((text or "").split())
    if not t:
        return "", ""
    first, *rest = SENTENCE_END.split(t, maxsplit=1)
    sentence_rest = rest[0] if rest else ""
    if clause:
        for m in CLAUSE_END.finditer(first):
            head, tail = first[:m.start()], first[m.end():]
            if len(head.split()) >= MIN_CLAUSE_WORDS and len((tail + " " + sentence_rest).split()) >= MIN_REST_WORDS:
                return head, (tail + " " + sentence_rest).strip()
    return first, sentence_rest


def spoken(text: str) -> str:
    t = text or ""
    for rx, rep in _RULES:
        t = rx.sub(rep, t)
    t = _HOURS.sub(_hours, t)
    t = _MINUTES.sub(_minutes, t)
    t = _MILES.sub(_miles, t)
    t = re.sub(r"\s*·\s*", ", ", t)
    t = re.sub(r"\s*/\s*", " or ", t)
    t = re.sub(r"[()\[\]]", "", t)
    t = re.sub(r"\s+,", ",", t)
    return re.sub(r"\s{2,}", " ", t).strip()
