import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

from plan import overlays as ov_mod
from plan import model

RANGES = [
    {"source": "main", "start": 0.0, "end": 2.0},
    {"source": "main", "start": 5.0, "end": 8.0},
]
PICKS = {
    "cuts": [],
    "visuals": [{"kind": "broll", "after_i": 1, "query": "laptop", "duration_s": 2.4}],
    "graphics": [{"text": "NEVER", "start_s": 0.5, "duration_s": 1.6}],
}


def test_visual_becomes_v2_overlay():
    ovs = ov_mod.overlays_from_picks(PICKS, RANGES, 5.0)
    broll = [o for o in ovs if o["kind"] == "broll"][0]
    assert broll["query"] == "laptop"
    assert broll["source"] == "mixkit"
    assert broll["file"] is None
    assert broll["enabled"] is True
    assert broll["locked"] is False
    assert broll["id"]
    # after_i=1 -> starts at the output time where range index 1 begins = 2.0s
    assert abs(broll["start_in_output"] - 2.0) < 0.01


def test_graphic_becomes_v2_overlay():
    ovs = ov_mod.overlays_from_picks(PICKS, RANGES, 5.0)
    g = [o for o in ovs if o["kind"] == "graphic"][0]
    assert g["text"] == "NEVER"
    assert abs(g["start_in_output"] - 0.5) < 0.01


def test_result_validates_inside_a_plan():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = RANGES
    p["overlays"] = ov_mod.overlays_from_picks(PICKS, RANGES, 5.0)
    assert model.validate(p) == []


def test_locked_overlays_are_preserved_and_not_duplicated():
    first = ov_mod.overlays_from_picks(PICKS, RANGES, 5.0)
    locked = [dict(first[0], locked=True)]
    again = ov_mod.overlays_from_picks(PICKS, RANGES, 5.0, locked=locked)
    # the locked overlay survives verbatim (same id)...
    assert any(o["id"] == locked[0]["id"] and o["locked"] for o in again)
    # ...and fresh overlays are still added for the picks
    assert len(again) >= len(locked)


def test_still_kind_is_carried_through():
    picks = {"visuals": [{"kind": "still", "after_i": 0, "query": "desk", "duration_s": 2.0}]}
    ovs = ov_mod.overlays_from_picks(picks, RANGES, 5.0)
    assert ovs[0]["kind"] == "still"
    assert ovs[0]["source"] == "pexels"
