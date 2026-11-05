#!/usr/bin/env python3
"""Builds i18n/ru.json from the _build_ru_*.py lists (templates by area, plus the words table). Edit those, run this.

    python i18n/build_ru.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ns: dict = {}
for name in ("_build_ru_agent.py", "_build_ru_more.py", "_build_ru_nav.py"):
    exec((HERE / name).read_text(), ns)
templates = ns["T"] + ns["T2"] + ns["T3"] + [
    {"en": "The closest is {name}, {mi} miles.", "ru": "Ближайший: {name}, {mi|plural:миля,мили,миль}."},
    {"en": "The closest is {name}, {mi} miles. Want to go there?", "ru": "Ближайший: {name}, {mi|plural:миля,мили,миль}. Поедем туда?"},
    {"en": "{event} near {area}.", "ru": "{event|nom} в районе {area}."},
]
words = {**ns["W"]}
for k, v in ns["W3"].items():
    words[k] = {**words.get(k, {}), **v} if isinstance(words.get(k), dict) else v
(HERE / "ru.json").write_text(json.dumps({"templates": templates, "words": words}, ensure_ascii=False, indent=1) + "\n")
print(f"ru.json: {len(templates)} templates, {len(words)} words")
