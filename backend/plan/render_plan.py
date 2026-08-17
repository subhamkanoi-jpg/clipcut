"""Render an EDL v2 using the helpers/ pipeline.

Stage order is fixed: cut -> concat -> composite overlays -> burn captions ->
two-pass loudnorm. Captions are always last before mastering so overlays cannot
cover them.
"""

import json
from pathlib import Path

import captions_ass
import render as helpers_render
from errors import Cancelled
from hidden_proc import run as hidden_run
from plan import materialize, model

# The old backend/render_engine.py (deleted; see the module docstring in
# helpers/render.py's "Animated zoom" section) rendered ClipCut at 30fps.
# helpers/render.py's own default (24) is for the standalone `video-use`
# skill and must not change — ClipCut pins its own rate here instead.
CLIPCUT_FPS = 30


def _probe_out(path: Path) -> dict:
    out = hidden_run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    stream = (data.get("streams") or [{}])[0]
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration": float((data.get("format") or {}).get("duration") or 0.0),
    }


def _extract_all(plan, work_dir, cover, center_x, check_cancel=None, segment_done=None):
    """Extract every range to its own segment file.

    `segment_done(done, total)`, when given, is called after each segment
    finishes so the caller can report per-segment progress -- the old
    renderer reported progress per extracted segment; the EDL v2 pipeline
    used to tick once at the start of this stage and then nothing until it
    was entirely done, which also meant no heartbeat for however long the
    longest, multi-minute stage took (the job lease is 60s). Piggybacking on
    the same progress_cb the caller already threads through to ctx.progress
    fixes both: the progress bar moves per segment, and each tick heartbeats
    the job lease.
    """
    sources = plan["sources"]
    paths = []
    ranges = plan["ranges"]
    total = len(ranges)
    for i, r in enumerate(ranges):
        if check_cancel:
            check_cancel()
        seg = work_dir / f"seg_{i:04d}.mp4"
        helpers_render.extract_segment(
            Path(sources[r["source"]]),
            float(r["start"]),
            float(r["end"]) - float(r["start"]),
            helpers_render.resolve_grade_filter(plan.get("grade")),
            seg,
            zoom=float(r.get("zoom") or 1.0),
            cover=cover,
            center_x=center_x,
            move=r.get("move"),
            fps=CLIPCUT_FPS,
        )
        paths.append(seg)
        if segment_done:
            segment_done(i + 1, total)
    return paths


def _concat(paths, work_dir, edit_dir):
    base = work_dir / "base.mp4"
    helpers_render.concat_segments(paths, base, edit_dir)
    return base


def _composite(base, plan, subs_path, work_dir, edit_dir):
    out = work_dir / "composite.mp4"
    overlays = [o for o in (plan.get("overlays") or []) if o.get("enabled")]
    helpers_render.build_final_composite(base, overlays, subs_path, out, edit_dir)
    return out


def _master(src, out_path):
    helpers_render.apply_loudnorm_two_pass(src, out_path)


def _moves_and_punches(plan: dict) -> tuple[list, list, int]:
    """Rebuild the old render_engine.render_export()-shaped `moves`/`punches`
    metadata from the EDL's per-range `move` fields, for the frontend
    (ReelStudio.jsx reads meta.moves / meta.punches / meta.punch_count).

    `moves` gets one entry per range that carries a move (mirrors
    zooms.plan()'s one-entry-per-kept-range shape when cinematic is on), with
    an `index` key restored so the UI can key its list by it. `punches` are
    every range's snaps flattened into absolute source-time, sorted, capped
    at 16 for display — `punch_count` is the *uncapped* total.
    """
    moves = []
    punches = []
    for i, r in enumerate(plan.get("ranges") or []):
        move = r.get("move")
        if not move:
            continue
        entry = dict(move)
        entry["index"] = i
        moves.append(entry)
        seg_start = float(move.get("start", r.get("start", 0.0)))
        for snap in move.get("snaps") or []:
            punches.append({
                "word": snap.get("word"),
                "t": round(seg_start + float(snap.get("t", 0.0)), 2),
                "amp": snap.get("amp"),
            })
    punches.sort(key=lambda p: p["t"])
    return moves, punches[:16], len(punches)


def render(plan: dict, project_dir: Path, out_path: Path, words: list,
           progress_cb=None, cancel_cb=None) -> dict:
    errors = model.validate(plan)
    if errors:
        raise ValueError("invalid plan: " + "; ".join(errors))

    def tick(p, stage):
        if progress_cb:
            progress_cb(p, stage)

    def check_cancel():
        if cancel_cb and cancel_cb():
            raise Cancelled()

    project_dir = Path(project_dir)
    edit_dir = materialize.edit_dir(project_dir)
    work_dir = edit_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    materialize.write(plan, project_dir)

    aspect = (plan.get("reframe") or {}).get("aspect", "9:16")
    cover = aspect == "9:16"
    center_x = float((plan.get("reframe") or {}).get("center_x", 0.5))

    # "cutting" spans 10 -> CUTTING_END: extraction is routinely the longest
    # stage (per-clip ffmpeg encodes), so it gets per-segment ticks instead
    # of one flat tick at the start and nothing again until the next stage.
    CUTTING_START, CUTTING_END = 10, 55

    def segment_done(done, total):
        if total <= 0:
            return
        span = CUTTING_END - CUTTING_START
        p = CUTTING_START + int(round(span * done / total))
        tick(min(p, CUTTING_END), "cutting")

    check_cancel()
    tick(CUTTING_START, "cutting")
    segments = _extract_all(plan, work_dir, cover, center_x, check_cancel, segment_done)

    check_cancel()
    base = _concat(segments, work_dir, edit_dir)

    subs_path = None
    caption_count = 0
    caps = plan.get("captions") or {}
    if caps.get("burn") and words:
        check_cancel()
        tick(60, "captioning")
        probe = _probe_out(base)
        candidate_subs_path = edit_dir / "captions.ass"
        style = captions_ass.CAPTION_STYLES.get(
            caps.get("style", "bold"), captions_ass.CAPTION_STYLES["bold"]
        )
        caption_count = captions_ass.build_ass(
            words,
            # captions_ass.timeline_chunks unpacks `for r_start, r_end in ranges`,
            # so these must be (start, end) TUPLES, not dicts. EDL v2 ranges are
            # dicts, so convert here.
            [(r["start"], r["end"]) for r in plan["ranges"]],
            candidate_subs_path, style, probe["width"], probe["height"],
            karaoke=bool(caps.get("karaoke", True)),
            fonts_dir=captions_ass.FONTS_DIR,
        )
        # build_ass writes an empty, headerless 0-byte file and returns 0 when
        # none of the kept ranges overlap any transcribed word. helpers/render.py
        # decides has_subs purely from subs_path.exists(), so an empty file would
        # still produce a `subtitles=<empty file>` ffmpeg clause. Only hand the
        # path onward when there is something to burn, matching render_engine.py's
        # `if caption_count: burn_captions(...)` guard.
        if caption_count:
            subs_path = candidate_subs_path
    else:
        tick(60, "captioning")

    # "compositing" lands on the actual composite pass (overlays + burned
    # captions, a real ffmpeg encode) rather than on the cheap lossless
    # concat above -- that concat used to be ticked "compositing" even
    # though the real compositing work hadn't started yet.
    check_cancel()
    tick(70, "compositing")
    composited = _composite(base, plan, subs_path, work_dir, edit_dir)

    check_cancel()
    tick(90, "mastering")
    _master(composited, out_path)

    tick(100, "done")
    moves, punches, punch_count = _moves_and_punches(plan)
    return {
        **_probe_out(out_path),
        "aspect": aspect,
        "moves": moves,
        "punches": punches,
        "punch_count": punch_count,
        "center_x": round(center_x, 3),
        "caption_events": caption_count,
        "karaoke": bool(caps.get("burn") and caps.get("karaoke", True)),
    }
