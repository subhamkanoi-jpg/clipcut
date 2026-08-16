import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plan import assemble, model

DOC = {
    "id": "p1",
    "video_path": "/data/p1/source.mp4",
    "caption_style": "neon",
    "reel_settings": {
        "aspect": "9:16", "cinematic": True, "karaoke": True,
        "zoom_intensity": 1.0, "punch_ins": True, "punch_sensitivity": 0.5,
        "burn_captions": True,
    },
}
CUT_STATE = {
    "keep_ranges": [(0.0, 2.4), (3.1, 6.0)],
    "kept_duration": 5.3,
}


def test_ranges_mirror_keep_ranges():
    p = assemble.from_project(DOC, CUT_STATE)
    assert len(p["ranges"]) == 2
    assert p["ranges"][0]["start"] == 0.0
    assert p["ranges"][0]["end"] == 2.4
    assert p["ranges"][1]["source"] == "main"


def test_captions_come_from_project_settings():
    p = assemble.from_project(DOC, CUT_STATE)
    assert p["captions"]["style"] == "neon"
    assert p["captions"]["karaoke"] is True
    assert p["captions"]["burn"] is True


def test_reframe_aspect_comes_from_reel_settings():
    p = assemble.from_project(DOC, CUT_STATE)
    assert p["reframe"]["aspect"] == "9:16"


def test_original_aspect_is_preserved():
    doc = {**DOC, "reel_settings": {**DOC["reel_settings"], "aspect": "original"}}
    assert assemble.from_project(doc, CUT_STATE)["reframe"]["aspect"] == "original"


def test_total_duration_is_the_sum_of_ranges():
    p = assemble.from_project(DOC, CUT_STATE)
    assert abs(p["total_duration_s"] - 5.3) < 0.01


def test_output_validates():
    assert model.validate(assemble.from_project(DOC, CUT_STATE)) == []


def test_center_x_defaults_to_half_when_absent():
    assert assemble.from_project(DOC, CUT_STATE)["reframe"]["center_x"] == 0.5


def test_center_x_is_carried_through_when_present():
    doc = {**DOC, "subject_center_x": 0.27}
    assert assemble.from_project(doc, CUT_STATE)["reframe"]["center_x"] == 0.27
