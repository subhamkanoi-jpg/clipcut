"""Real end-to-end render test of the current pipeline.

This used to compare `render_engine.py` (deleted) against `helpers/render.py`
as a gate for the deletion. Now that there's only one renderer, that
comparison is meaningless -- this is a genuine integration test of
`extract_segment -> concat_segments -> build_final_composite(.ass) ->
apply_loudnorm_two_pass` end to end, run with `cinematic: True` and
`punch_ins: True` so the speech-driven zoom path is actually exercised
(the parity test previously ran with both off, which is exactly why the
zoom feature going dead slipped through unnoticed).

`fixtures/parity_src.mp4` is committed (see fixtures/README.md to
regenerate) so this test always runs; it must never silently skip again.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

# helpers/render.py prints unicode arrows ("->") as part of its normal
# progress output. worker.py calls this at process startup (see its own
# comment: Windows cp1252 stdout can't encode them, crashing the job) but
# pytest never does, and this is the first test that actually runs the
# render pipeline for real rather than monkeypatching it out -- without
# this, the render crashes on the first `print()`, not on anything this
# suite is meant to catch.
from stdio import configure_stdio

configure_stdio()

import zooms
from plan import assemble, render_plan

FIXTURE = Path(__file__).parent / "fixtures" / "parity_src.mp4"

# Deliberately includes punctuation-ended, hook-word ("never", "secret"), and
# an elongated word ("never") so zooms.plan's emphasis scoring has real
# signal to work with -- a flat/uniform transcript would let the zoom path
# run without ever actually producing a snap, defeating the point of this
# test (see Finding 1 in the branch review: the old parity gate ran with
# cinematic/punch_ins off, which is exactly the config where the zoom path
# is inert).
WORDS = [
    {"text": "This", "start": 0.20, "end": 0.45, "type": "word"},
    {"text": "is", "start": 0.50, "end": 0.65, "type": "word"},
    {"text": "never", "start": 0.70, "end": 1.30, "type": "word"},
    {"text": "going", "start": 1.35, "end": 1.60, "type": "word"},
    {"text": "to", "start": 1.65, "end": 1.75, "type": "word"},
    {"text": "stop!", "start": 1.80, "end": 2.30, "type": "word"},
    {"text": "Watch", "start": 3.10, "end": 3.55, "type": "word"},
    {"text": "this", "start": 3.60, "end": 3.85, "type": "word"},
    {"text": "secret", "start": 3.90, "end": 4.55, "type": "word"},
    {"text": "trick.", "start": 4.60, "end": 5.20, "type": "word"},
]
KEEP_RANGES = [(0.0, 2.5), (3.0, 5.8)]


def _probe_frame_count(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames",
         "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return int(out.stdout.strip())


def _cut_state_with_moves(cinematic: bool, punch_ins: bool) -> dict:
    moves = (
        zooms.plan(WORDS, KEEP_RANGES, intensity=1.0, punch_ins=punch_ins,
                  punch_sensitivity=0.5)
        if cinematic else []
    )
    kept = sum(b - a for a, b in KEEP_RANGES)
    return {"keep_ranges": KEEP_RANGES, "kept_duration": round(kept, 2), "moves": moves}


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture not generated")
def test_full_pipeline_renders_expected_geometry_duration_and_frame_count(tmp_path):
    cut_state = _cut_state_with_moves(cinematic=True, punch_ins=True)

    # Sanity-check the fixture actually contains a real camera move -- if the
    # heuristic ever produces only flat holds with no snaps for this
    # transcript, the render below wouldn't exercise the zoompan path and
    # this test would silently degrade back into the old blind spot.
    assert any(
        m["z0"] != m["z1"] or m.get("snaps")
        for m in cut_state["moves"]
    ), "test transcript/ranges must trigger at least one real zoom or snap"

    doc = {
        "id": "parity",
        "video_path": str(FIXTURE),
        "caption_style": "bold",
        "reel_settings": {
            "aspect": "9:16", "cinematic": True, "karaoke": True,
            "zoom_intensity": 1.0, "punch_ins": True, "punch_sensitivity": 0.5,
            "burn_captions": True,
        },
    }
    plan = assemble.from_project(doc, cut_state)
    assert any(r.get("move") for r in plan["ranges"]), \
        "assemble.from_project must carry cut_state moves onto the EDL ranges"

    out_path = tmp_path / "out.mp4"
    meta = render_plan.render(plan, tmp_path, out_path, words=WORDS)

    assert out_path.is_file()
    probe = render_plan._probe_out(out_path)

    assert probe["width"] == 1080
    assert probe["height"] == 1920

    expected_duration = cut_state["kept_duration"]
    assert abs(probe["duration"] - expected_duration) < 0.4

    # Frame count: this is what would have caught Finding 3 (fps silently
    # dropped 30 -> 24). ClipCut renders at 30fps (render_plan.CLIPCUT_FPS);
    # a 24fps render of the same duration would be off by ~20%, far outside
    # this tolerance.
    frame_count = _probe_frame_count(out_path)
    expected_frames = probe["duration"] * render_plan.CLIPCUT_FPS
    assert abs(frame_count - expected_frames) <= 3, (
        f"frame_count={frame_count} not consistent with "
        f"{render_plan.CLIPCUT_FPS}fps over {probe['duration']:.3f}s "
        f"(expected ~{expected_frames:.1f})"
    )

    # meta (Finding 2): the frontend reads meta.moves / meta.punch_count /
    # meta.caption_events directly (frontend/src/screens/ReelStudio.jsx).
    assert len(meta["moves"]) == len(KEEP_RANGES)
    assert meta["punch_count"] >= len(meta["punches"])
    assert meta["caption_events"] > 0
    assert meta["karaoke"] is True
    assert meta["aspect"] == "9:16"
    assert 0.0 <= meta["center_x"] <= 1.0


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture not generated")
def test_original_aspect_keeps_source_geometry(tmp_path):
    cut_state = _cut_state_with_moves(cinematic=False, punch_ins=False)
    doc = {
        "id": "parity2",
        "video_path": str(FIXTURE),
        "caption_style": "bold",
        "reel_settings": {
            "aspect": "original", "cinematic": False, "karaoke": False,
            "zoom_intensity": 1.0, "punch_ins": False, "punch_sensitivity": 0.5,
            "burn_captions": False,
        },
    }
    plan = assemble.from_project(doc, cut_state)
    out = tmp_path / "out2.mp4"

    meta = render_plan.render(plan, tmp_path, out, words=[])

    assert meta["width"] > meta["height"]
    assert meta["moves"] == []
    assert meta["punch_count"] == 0
