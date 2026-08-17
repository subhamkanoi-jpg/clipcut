"""Assemble picks into EDL v2 overlays, and resolve their media at render time.

`overlays_from_picks` turns a picks dict into EDL v2 overlays with file=None
and full review metadata (enabled/locked/query/source), preserving any
already-locked overlays. `resolve_overlays` walks those overlays at render
time and fetches each enabled one's media to a local file: broll via
`resolve_broll_file` (converting any still-image result to an mp4 clip via
`photo_to_clip`), still via `match_photo` + `photo_to_clip`, graphic via
`make_keyword_graphic`. Resolution is fault-isolated per overlay -- if the
primary strategy raises or yields nothing, it falls back to a keyword
graphic for that overlay alone, so one bad overlay never aborts the batch.
"""

from __future__ import annotations

import logging
from pathlib import Path

from plan import model

log = logging.getLogger(__name__)

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
    """Return overlays with each enabled one's media resolved to a local file.

    Fault-isolated per overlay: if the primary acquisition strategy raises
    or yields no usable file, that overlay degrades to a keyword-graphic
    fallback instead of propagating the exception and aborting the rest of
    the batch. Only if the fallback itself fails is the overlay's file left
    as None; every other enabled overlay still gets resolved.
    """
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

        try:
            if kind == "graphic":
                resolved = make_keyword_graphic(str(item.get("text") or "NOW"), edit_dir, dur)
            elif kind == "broll" and fetch:
                got = resolve_broll_file({"query": item.get("query")}, _broll_dir(edit_dir))
                if got is not None:
                    got = Path(got)
                    if got.is_file() and got.suffix.lower() in IMAGE_EXTS:
                        # resolve_broll_file can hand back a still (Pexels
                        # photo catalog / API hit) instead of a video; the
                        # compositor treats every overlay file as a video
                        # input, so convert it to an mp4 clip first.
                        clip = _broll_dir(edit_dir) / f"{got.stem}.mp4"
                        got = photo_to_clip(got, clip, dur)
                resolved = got
            elif kind == "still" and fetch:
                photo = match_photo(str(item.get("query") or ""))
                src = Path(str(photo.get("file"))) if photo and photo.get("file") else None
                if src and src.is_file():
                    clip = _broll_dir(edit_dir) / f"{src.stem}.mp4"
                    resolved = photo_to_clip(src, clip, dur)
        except Exception:
            resolved = None

        is_file = False
        if resolved is not None:
            try:
                is_file = Path(resolved).is_file()
            except (TypeError, ValueError):
                pass

        if not is_file:
            # Never drop an overlay: fall back to a keyword graphic. Guard
            # this too -- if even the fallback raises, leave this overlay's
            # file as None and move on rather than aborting the batch.
            label = str(item.get("query") or item.get("text") or "B-ROLL")
            log.info("overlay degraded to keyword-graphic fallback: kind=%s query=%r",
                      kind, label)
            item["degraded"] = True
            try:
                resolved = make_keyword_graphic(label.upper()[:18], edit_dir, dur)
                if resolved is not None and not Path(resolved).is_file():
                    resolved = None
            except Exception:
                resolved = None
            if resolved is None:
                log.warning("overlay has no usable file after fallback: kind=%s query=%r",
                             kind, label)

        item["file"] = str(Path(resolved).resolve()) if resolved is not None else None
        out.append(item)
    return out
