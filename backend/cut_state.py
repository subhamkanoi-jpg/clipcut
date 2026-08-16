"""Shared cut-state math and defaults used by both the API and the worker.

Kept dependency-free of `server` (FastAPI app, Mongo client) and of `worker`
(job loop) so either side can import it without pulling the other in.
"""

from datetime import datetime, timezone

import cuts as cuts_mod
import zooms

DEFAULT_CUT_SETTINGS = {"pause_threshold": 0.8, "remove_fillers": True, "disabled": []}
DEFAULT_REEL = {
    "aspect": "9:16",
    "cinematic": True,
    "karaoke": True,
    "zoom_intensity": 1.0,
    "punch_ins": True,
    "punch_sensitivity": 0.5,
    "burn_captions": True,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_cut_state(doc: dict) -> dict:
    settings = doc.get("cut_settings") or DEFAULT_CUT_SETTINGS
    words = doc.get("words") or []
    duration = doc.get("duration") or 0
    spans = cuts_mod.compute_spans(words, duration, settings["pause_threshold"], settings["remove_fillers"])
    disabled = set(settings.get("disabled") or [])
    ranges = cuts_mod.keep_ranges(duration, spans, disabled)
    for s in spans:
        s["disabled"] = s["id"] in disabled
    kept = sum(b - a for a, b in ranges)
    reel = doc.get("reel_settings") or DEFAULT_REEL
    return {
        "spans": spans,
        "keep_ranges": ranges,
        "kept_duration": round(kept, 2),
        "removed_duration": round(max(0, duration - kept), 2),
        "settings": settings,
        "moves": zooms.plan(words, ranges, reel.get("zoom_intensity", 1.0),
                            reel.get("punch_ins", True),
                            reel.get("punch_sensitivity", 0.5)) if reel.get("cinematic") else [],
    }
