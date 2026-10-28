"""Route names feed the recognizer's vocabulary hint, capped and sanitised; the endpoint takes it as a form field."""
from types import SimpleNamespace

from conftest import run

from services import voice
from services.voice import PROMPT, stt_prompt


def test_hint_is_appended_deduped_and_sanitised():
    out = stt_prompt("Pilot Travel Center, Fernley; Lodi | Love's Travel Stop\nReno, NV, Reno, NV")
    assert out.startswith(PROMPT + " ")
    tail = out[len(PROMPT) + 1:]
    assert tail == "Pilot Travel Center, Fernley, Lodi, Love's Travel Stop, Reno, NV."  # "Reno, NV" splits, dedupes
    assert stt_prompt(None) == PROMPT and stt_prompt("") == PROMPT and stt_prompt(" , , ") == PROMPT


def test_hint_drops_junk_and_known_words():
    out = stt_prompt("<script>alert(1)</script>, Love's, Pilot, {weird}, Kingvale")
    assert "<" not in out and "{" not in out
    assert out.endswith(" scriptalert 1 script, Kingvale.") or out.endswith("Kingvale.")
    assert out.count("Love's") == 1  # already in the fixed list, not repeated


def test_hint_is_capped():
    long = ", ".join(f"Town{i:03d}" for i in range(80))
    out = stt_prompt(long)
    assert len(out) <= len(PROMPT) + 1 + voice.HINT_MAX_CHARS + 1
    assert out.endswith(".") and not out.endswith(",.")


def test_transcribe_passes_the_hint_to_both_models(monkeypatch):
    calls = []

    class _Stt:
        async def create(self, **kw):
            calls.append(kw)
            if kw["model"] == voice.STT_MODEL:
                raise RuntimeError("primary down")
            return SimpleNamespace(text="stop at the Pilot in Fernley", segments=[])

    monkeypatch.setattr(voice.llm, "client", lambda: SimpleNamespace(audio=SimpleNamespace(transcriptions=_Stt())))
    tr = run(voice.transcribe_full(b"abc", "a.m4a", "Pilot Travel Center, Fernley"))
    assert tr.text == "stop at the Pilot in Fernley"
    assert all(c["prompt"].endswith("Fernley.") for c in calls) and len(calls) == 2


def test_transcribe_endpoint_accepts_the_hint(api_client, monkeypatch):
    seen = {}

    async def fake(data, name, hint=None):
        seen["hint"] = hint
        return voice.Transcript("ok", 0.9)

    monkeypatch.setattr(voice, "transcribe_full", fake)
    r = api_client.post("/api/voice/transcribe", files={"audio": ("a.m4a", b"abc", "audio/m4a")},
                        data={"hint": "Pilot Travel Center, Lodi"})
    assert r.status_code == 200 and seen["hint"] == "Pilot Travel Center, Lodi"
    r = api_client.post("/api/voice/transcribe", files={"audio": ("a.m4a", b"abc", "audio/m4a")})
    assert r.status_code == 200 and seen["hint"] is None
    too_long = "x" * 601
    r = api_client.post("/api/voice/transcribe", files={"audio": ("a.m4a", b"abc", "audio/m4a")},
                        data={"hint": too_long})
    assert r.status_code == 422
