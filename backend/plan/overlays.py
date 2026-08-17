"""Assemble picks into EDL v2 overlays, and resolve their media at render time.

Planning produces overlays with file=None and full review metadata
(enabled/locked/query/source). Resolution (Task 6) fetches the media just
before compositing.
"""

from __future__ import annotations

from pathlib import Path

from plan import model

# Where each visual kind's media comes from, for the review UI's "swap".
_SOURCE = {"broll": "mixkit", "still": "pexels"}


def overlays_from_picks(picks: dict, ranges: list, total_s: float,
                        locked: list | None = None) -> list:
    """Build EDL v2 overlays from a picks dict, preserving locked overlays."""
    from visual_picks import output_time_at  # helpers, on sys.path

    out: list = list(locked or [])

    for vis in (picks.get("visuals") or []):
        kind = vis.get("kind")
        if kind not in _SOURCE:
            continue
        try:
            after_i = int(vis.get("after_i") or 0)
        except (TypeError, ValueError):
            after_i = 0
        start = output_time_at(ranges, after_i)
        dur = float(vis.get("duration_s") or 2.0)
        if total_s > 0:
            dur = min(dur, max(0.8, total_s - start))
        out.append(model.overlay(
            kind, round(start, 2), round(dur, 2),
            query=str(vis.get("query") or "").strip(),
            source=_SOURCE[kind],
            after_i=after_i,
        ))

    for g in (picks.get("graphics") or []):
        text = str(g.get("text") or "").strip()
        if not text:
            continue
        out.append(model.overlay(
            "graphic",
            round(float(g.get("start_s") or 0.0), 2),
            round(float(g.get("duration_s") or 1.6), 2),
            text=text,
            source="pil",
        ))

    return out
