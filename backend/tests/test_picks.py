import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plan.providers import base


def test_empty_object_is_valid():
    assert base.validate_picks({}) == []


def test_full_valid_object():
    picks = {
        "cuts": [{"range_i": 0, "variation": "push", "score": 0.8}],
        "visuals": [{"kind": "broll", "after_i": 2, "query": "typing on laptop", "duration_s": 2.4}],
        "graphics": [{"text": "NEVER", "start_s": 1.1, "duration_s": 1.6}],
    }
    assert base.validate_picks(picks) == []


def test_non_dict_is_invalid():
    assert base.validate_picks([1, 2]) != []


def test_cuts_must_be_a_list():
    assert any("cuts" in e for e in base.validate_picks({"cuts": {}}))


def test_visual_needs_kind_and_query():
    assert any("visuals[0]" in e for e in base.validate_picks({"visuals": [{"after_i": 1}]}))


def test_graphic_needs_text():
    assert any("graphics[0]" in e for e in base.validate_picks({"graphics": [{"start_s": 1.0}]}))


def test_bad_duration_rejected():
    picks = {"visuals": [{"kind": "broll", "query": "x", "after_i": 0, "duration_s": -1}]}
    assert any("duration" in e for e in base.validate_picks(picks))


def test_plan_context_holds_fields(tmp_path):
    ctx = base.PlanContext(edit_dir=tmp_path, words=[], text="hi", ranges=[], total_s=0.0)
    assert ctx.edit_dir == tmp_path
    assert ctx.text == "hi"
