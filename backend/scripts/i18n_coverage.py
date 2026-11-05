#!/usr/bin/env python3
"""How much of what the copilot says the translation catalog covers.

    I18N_CAPTURE=/tmp/corpus.jsonl pytest tests/offline     # records every reply of the test run
    python scripts/i18n_coverage.py /tmp/corpus.jsonl ru     # covered share + the sentences still in English

A sentence counts as covered when a catalog template (or a word) translates it; model-written lines (chat, bye) in
the corpus are English only because the tests stub the model in English, so they are listed apart.
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import i18n  # noqa: E402


def main() -> None:
    path, code = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "ru")
    texts = Counter(json.loads(line)["text"] for line in open(path) if line.strip())
    misses: Counter = Counter()
    covered = total = 0
    for text, n in texts.items():
        got: list[str] = []
        i18n.translate(text, code, fallback=lambda s, c: got.append(s) or None)
        sents = len(i18n._SENT.split(text.strip()))
        total += sents * n
        covered += (sents - len(got)) * n
        for s in got:
            misses[s] += n
    print(f"{code}: {covered}/{total} sentences covered ({100 * covered / max(1, total):.1f}%), {len(misses)} distinct misses")
    for s, n in misses.most_common():
        print(f"{n:4d}  {s}")


if __name__ == "__main__":
    main()
