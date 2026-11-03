"""The kinds the app logs (frontend `tripLog.log("…")`, branch claude/app-nav-fixes) are ones the server knows:
route_restored, route_replaced, stop_removed and turn_unknown_action came in as "unknown kind" (QA 2026-11-02).
The backend stack carries an older frontend, so the list is spelled out here instead of read from it."""
from services.trip_events import KNOWN_KINDS

APP_KINDS = {
    "reroute", "turn_unknown_action", "turn_failed", "stop_removed", "speech_dropped", "route_started",
    "route_restored", "route_replaced", "route_ended", "maneuver_said", "alternative_picked", "position", "arrived",
}


def test_every_kind_the_app_logs_is_known():
    assert APP_KINDS <= KNOWN_KINDS, APP_KINDS - KNOWN_KINDS
