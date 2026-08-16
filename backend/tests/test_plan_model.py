import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plan import model


def test_new_plan_has_v2_shape():
    p = model.new_plan("p1", "data/p1/source.mp4")
    assert p["version"] == 2
    assert p["project_id"] == "p1"
    assert p["sources"] == {"main": "data/p1/source.mp4"}
    assert p["ranges"] == []
    assert p["overlays"] == []
    assert p["audio_overlays"] == []
    assert p["reframe"]["aspect"] == "9:16"
    assert p["captions"]["karaoke"] is True


def test_overlay_factory_assigns_unique_ids():
    a = model.overlay("broll", 1.0, 2.0, query="laptop")
    b = model.overlay("broll", 3.0, 2.0, query="desk")
    assert a["id"] != b["id"]
    assert a["enabled"] is True
    assert a["locked"] is False
    assert a["query"] == "laptop"


def test_validate_accepts_minimal_valid_plan():
    p = model.new_plan("p1", "data/p1/source.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    assert model.validate(p) == []


def test_validate_rejects_empty_ranges():
    p = model.new_plan("p1", "s.mp4")
    assert any("ranges" in e for e in model.validate(p))


def test_validate_rejects_unknown_source():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "nope", "start": 0.0, "end": 1.0}]
    assert any("nope" in e for e in model.validate(p))


def test_validate_rejects_inverted_range():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 5.0, "end": 5.0}]
    assert any("start < end" in e for e in model.validate(p))


def test_validate_rejects_negative_overlay_duration():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    p["overlays"] = [model.overlay("broll", 0.5, -1.0)]
    assert any("duration" in e for e in model.validate(p))


def test_validate_rejects_bad_aspect():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    p["reframe"]["aspect"] = "4:3"
    assert any("aspect" in e for e in model.validate(p))


def test_validate_rejects_center_x_out_of_range():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    p["reframe"]["center_x"] = 1.7
    assert any("center_x" in e for e in model.validate(p))
