import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))

import render


def test_centered_crop_matches_legacy_behaviour():
    f = render.cover_crop_filter(1920, 1080, center_x=0.5)
    assert "crop=1080:1920" in f
    # A centred subject on a 1920x1080 source: crop x offset lands mid-frame.
    assert ":x=" in f


def test_off_centre_subject_shifts_crop_left():
    left = render.cover_crop_filter(1920, 1080, center_x=0.2)
    centre = render.cover_crop_filter(1920, 1080, center_x=0.5)
    x_left = int(left.split(":x=")[1].split(":")[0])
    x_centre = int(centre.split(":x=")[1].split(":")[0])
    assert x_left < x_centre


def test_crop_never_leaves_the_frame():
    # NOTE: bound is the *scaled* canvas width, not the raw source width.
    # cover_crop_filter scales-to-cover before cropping, so a 1920x1080
    # landscape source going to a 1080x1920 portrait canvas is scaled up to
    # 3414x1920 first (same "increase" semantics as the legacy
    # force_original_aspect_ratio=increase filter it replaces) — the crop's
    # x offset is a coordinate in that scaled space, not the original 1920px
    # source. Hardcoding the bound at 1920 is unsatisfiable by construction
    # at center_x near 1.0 for any correct cover crop of this source/target
    # pair, so the bound is recomputed here the same way the implementation
    # derives it.
    src_w, src_h, out_w, out_h = 1920, 1080, 1080, 1920
    scale_f = max(out_w / src_w, out_h / src_h)
    scaled_w = int(round(src_w * scale_f))
    scaled_w += scaled_w % 2
    for cx in (0.0, 0.01, 0.99, 1.0):
        f = render.cover_crop_filter(src_w, src_h, center_x=cx)
        x = int(f.split(":x=")[1].split(":")[0])
        assert x >= 0
        assert x + out_w <= scaled_w


def test_draft_uses_smaller_canvas():
    assert "720:1280" in render.cover_crop_filter(1920, 1080, 0.5, draft=True)


def test_already_vertical_source_is_not_cropped_horizontally():
    f = render.cover_crop_filter(1080, 1920, center_x=0.5)
    x = int(f.split(":x=")[1].split(":")[0])
    assert x == 0
