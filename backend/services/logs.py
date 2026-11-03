"""What the logs may say about a driver's words. LOG_TRANSCRIPTS=1 (dev, the test stand) logs transcripts and
spoken queries as before; off by default, so production logs carry their length only. The trip log keeps the
text of a failed turn either way (services/trip_events.py); this is about the log stream, which goes to the
hosting provider and whoever reads it."""
from services import flags


def transcripts_on() -> bool:
    return flags.on("LOG_TRANSCRIPTS", "0")


def said(text) -> str:
    """repr(text) with LOG_TRANSCRIPTS on, else a placeholder with its length."""
    if text is None:
        return "None"
    if transcripts_on():
        return repr(text)
    return f"<{len(str(text))} chars>"
