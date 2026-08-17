import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

from plan.providers.base import PlanContext
from plan.providers.heuristic import HeuristicProvider
from plan import assemble, overlays as ov_mod, render_plan

FIXTURE = Path(__file__).parent / "fixtures" / "parity_src.mp4"

WORDS = [
    {"text": "This", "start": 0.2, "end": 0.5, "type": "word"},
    {"text": "laptop", "start": 0.6, "end": 1.1, "type": "word"},
    {"text": "everything", "start": 1.2, "end": 1.9, "type": "word"},
    {"text": "coding", "start": 3.0, "end": 3.6, "type": "word"},
]
CUT_STATE = {"keep_ranges": [(0.0, 2.0), (2.8, 4.5)], "kept_duration": 3.7, "moves": []}


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture not generated")
def test_heuristic_plan_renders_with_an_overlay(tmp_path, monkeypatch):
    # Keep it offline: force every b-roll fetch to fail so the graphic fallback
    # (pure PIL, no network) supplies the overlay clip.
    monkeypatch.setattr(ov_mod, "resolve_broll_file", lambda vis, dest: None)
    monkeypatch.setattr(ov_mod, "match_photo", lambda q, items=None: None)

    doc = {"id": "e2e", "video_path": str(FIXTURE), "caption_style": "bold",
           "reel_settings": {"aspect": "9:16", "cinematic": False, "karaoke": False,
                             "zoom_intensity": 1.0, "punch_ins": False,
                             "punch_sensitivity": 0.5, "burn_captions": False}}
    base = assemble.from_project(doc, CUT_STATE)
    ctx = PlanContext(edit_dir=tmp_path / "edit", words=WORDS, text="This laptop everything coding",
                      ranges=base["ranges"], total_s=base["total_duration_s"])
    (tmp_path / "edit").mkdir(parents=True, exist_ok=True)
    picks = HeuristicProvider().plan(ctx)
    base["overlays"] = ov_mod.overlays_from_picks(picks, base["ranges"], ctx.total_s)
    assert len(base["overlays"]) >= 1

    out = tmp_path / "out.mp4"
    meta = render_plan.render(base, tmp_path, out, words=[])
    assert out.is_file()
    assert meta["width"] == 1080 and meta["height"] == 1920
    assert meta["duration"] > 0
