"""Gate for deleting render_engine.py: both renderers must agree."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

from plan import assemble, render_plan

FIXTURE = Path(__file__).parent / "fixtures" / "parity_src.mp4"

WORDS = [
    {"text": "one", "start": 0.5, "end": 0.9, "type": "word"},
    {"text": "two", "start": 1.0, "end": 1.4, "type": "word"},
    {"text": "three", "start": 3.0, "end": 3.5, "type": "word"},
]
CUT_STATE = {"keep_ranges": [(0.0, 2.0), (2.8, 4.5)], "kept_duration": 3.7}


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture not generated")
def test_both_renderers_agree(tmp_path):
    """The actual gate: old and new renderer must produce the same geometry
    and duration from identical input. This is what licenses the deletion.

    Once Task 12 Step 5 runs `git rm backend/render_engine.py`, this import
    can no longer succeed on any future run — that's expected, not a
    regression. The gate already did its job (this run, right now); after
    that, importorskip turns a permanent ImportError into a clean, readable
    skip instead of a permanently red test."""
    render_engine = pytest.importorskip(
        "render_engine",
        reason="render_engine.py was deleted after this gate licensed its removal (Task 12)",
    )

    doc = {
        "id": "parity",
        "video_path": str(FIXTURE),
        "caption_style": "bold",
        "reel_settings": {
            "aspect": "9:16", "cinematic": False, "karaoke": True,
            "zoom_intensity": 1.0, "punch_ins": False, "punch_sensitivity": 0.5,
            "burn_captions": True,
        },
    }

    old_out = tmp_path / "old.mp4"
    old_meta = render_engine.render_export(
        source=FIXTURE,
        words=WORDS,
        ranges=CUT_STATE["keep_ranges"],
        style_key="bold",
        burn=True,
        work_dir=tmp_path / "work_old",
        out_path=old_out,
        aspect="9:16",
        cinematic=False,
        karaoke=True,
        zoom_intensity=1.0,
        punch_ins=False,
        punch_sensitivity=0.5,
        progress_cb=lambda p: None,
    )

    new_out = tmp_path / "new.mp4"
    plan = assemble.from_project(doc, CUT_STATE)
    new_meta = render_plan.render(plan, tmp_path, new_out, words=WORDS)

    assert old_out.is_file() and new_out.is_file()

    # Compare the rendered ARTIFACTS, not the renderers' self-reported metadata.
    # The two return dicts have different shapes by design — render_export
    # returns width/height/aspect/moves/punches/punch_count/center_x/
    # caption_events and has never returned a duration — so probing both files
    # is both the only apples-to-apples comparison and the stronger test.
    old_probe = render_plan._probe_out(old_out)
    new_probe = render_plan._probe_out(new_out)

    assert new_probe["width"] == old_probe["width"] == 1080
    assert new_probe["height"] == old_probe["height"] == 1920
    # Same cuts in, so durations must match within encoder/fade slop.
    assert abs(new_probe["duration"] - old_probe["duration"]) < 0.25
    # Sanity-check the geometry the old renderer reported about itself too.
    assert old_meta["width"] == 1080 and old_meta["height"] == 1920


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture not generated")
def test_original_aspect_keeps_source_geometry(tmp_path):
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
    plan = assemble.from_project(doc, CUT_STATE)
    out = tmp_path / "out2.mp4"

    meta = render_plan.render(plan, tmp_path, out, words=[])

    assert meta["width"] > meta["height"]
