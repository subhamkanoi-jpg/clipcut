"""Assemble picks into EDL v2 overlays, and resolve their media at render time.

Planning produces overlays with file=None and full review metadata
(enabled/locked/query/source). Resolution (Task 6) fetches the media just
before compositing.
"""

from __future__ import annotations

from pathlib import Path

from plan import model

# --- render-time resolution ---
# Imported at module scope so tests can monkeypatch them on this module.
try:
    from visual_picks import (
        resolve_broll_file, photo_to_clip, make_keyword_graphic, IMAGE_EXTS,
    )
    from pexels_library import match_photo
except Exception:  # helpers not importable in some unit contexts
    resolve_broll_file = photo_to_clip = make_keyword_graphic = match_photo = None
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

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


def _broll_dir(edit_dir: Path) -> Path:
    d = Path(edit_dir) / "bin" / "broll"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_overlays(overlays: list, edit_dir: Path, *, fetch: bool = True) -> list:
    """Return overlays with each enabled one's media resolved to a local file."""
    edit_dir = Path(edit_dir)
    out: list = []
    for ov in overlays:
        item = dict(ov)
        if not item.get("enabled", True):
            out.append(item)
            continue
        if item.get("file") and Path(item["file"]).is_file():
            out.append(item)
            continue

        kind = item.get("kind")
        dur = float(item.get("duration") or 2.0)
        resolved: Path | None = None

        if kind == "graphic":
            resolved = make_keyword_graphic(str(item.get("text") or "NOW"), edit_dir, dur)
        elif kind == "broll" and fetch:
            resolved = resolve_broll_file({"query": item.get("query")}, _broll_dir(edit_dir))
        elif kind == "still" and fetch:
            photo = match_photo(str(item.get("query") or ""))
            src = Path(str(photo.get("file"))) if photo and photo.get("file") else None
            if src and src.is_file():
                clip = _broll_dir(edit_dir) / f"{src.stem}.mp4"
                resolved = photo_to_clip(src, clip, dur)

        is_file = False
        if resolved is not None:
            try:
                is_file = Path(resolved).is_file()
            except (TypeError, ValueError):
                pass

        if not is_file:
            # Never drop an overlay: fall back to a keyword graphic.
            label = str(item.get("query") or item.get("text") or "B-ROLL")
            resolved = make_keyword_graphic(label.upper()[:18], edit_dir, dur)

        item["file"] = str(Path(resolved).resolve())
        out.append(item)
    return out
