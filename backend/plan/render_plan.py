"""Render an EDL v2 using the helpers/ pipeline.

Stage order is fixed: cut -> concat -> composite overlays -> burn captions ->
two-pass loudnorm. Captions are always last before mastering so overlays cannot
cover them.
"""

import json
import subprocess
from pathlib import Path

import captions_ass
import render as helpers_render
import worker
from plan import materialize, model


def _probe_out(path: Path) -> dict:
    out = subprocess.run(
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


def _extract_all(plan, project_dir, work_dir, cover, center_x):
    sources = plan["sources"]
    paths = []
    for i, r in enumerate(plan["ranges"]):
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
        )
        paths.append(seg)
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
            raise worker.Cancelled()

    project_dir = Path(project_dir)
    edit_dir = materialize.edit_dir(project_dir)
    work_dir = edit_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    materialize.write(plan, project_dir)

    aspect = (plan.get("reframe") or {}).get("aspect", "9:16")
    cover = aspect == "9:16"
    center_x = float((plan.get("reframe") or {}).get("center_x", 0.5))

    check_cancel()
    tick(10, "cutting")
    segments = _extract_all(plan, project_dir, work_dir, cover, center_x)

    check_cancel()
    tick(55, "compositing")
    base = _concat(segments, work_dir, edit_dir)

    subs_path = None
    caps = plan.get("captions") or {}
    if caps.get("burn") and words:
        check_cancel()
        tick(70, "captioning")
        probe = _probe_out(base)
        subs_path = edit_dir / "captions.ass"
        style = captions_ass.CAPTION_STYLES.get(
            caps.get("style", "bold"), captions_ass.CAPTION_STYLES["bold"]
        )
        captions_ass.build_ass(
            words,
            # captions_ass.timeline_chunks unpacks `for r_start, r_end in ranges`,
            # so these must be (start, end) TUPLES, not dicts. EDL v2 ranges are
            # dicts, so convert here.
            [(r["start"], r["end"]) for r in plan["ranges"]],
            subs_path, style, probe["width"], probe["height"],
            karaoke=bool(caps.get("karaoke", True)),
            fonts_dir=captions_ass.FONTS_DIR,
        )
    else:
        tick(70, "captioning")

    check_cancel()
    composited = _composite(base, plan, subs_path, work_dir, edit_dir)

    check_cancel()
    tick(90, "mastering")
    _master(composited, out_path)

    tick(100, "done")
    return _probe_out(out_path)
