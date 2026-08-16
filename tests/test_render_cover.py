import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))

import render


def _parse_scale(f: str) -> tuple[int, int]:
    """Independent oracle: parse the actual scale=W:H out of the filter
    string via regex, rather than recomputing the scale formula the way the
    production code does. This is the only way a regression in the
    scale-to-cover arithmetic itself can be caught by a test.
    """
    m = re.search(r"scale=(\d+):(\d+)", f)
    assert m, f"no scale=W:H found in filter string: {f!r}"
    return int(m.group(1)), int(m.group(2))


def _parse_crop_xy(f: str) -> tuple[int, int]:
    m = re.search(r":x=(\d+):y=(\d+)", f)
    assert m, f"no crop x/y found in filter string: {f!r}"
    return int(m.group(1)), int(m.group(2))


def test_scale_dimensions_match_known_case():
    # 1920x1080 source, 1080x1920 target: scale_f = max(1080/1920, 1920/1080)
    # = 1920/1080 = 1.7777... -> scaled_w = round(1920*1.7778) = 3413,
    # even-adjusted to 3414. scaled_h = round(1080*1.7778) = 1920 (already even).
    f = render.cover_crop_filter(1920, 1080, center_x=0.5)
    scaled_w, scaled_h = _parse_scale(f)
    assert (scaled_w, scaled_h) == (3414, 1920)


def test_centered_crop_matches_legacy_behaviour():
    f = render.cover_crop_filter(1920, 1080, center_x=0.5)
    assert "crop=1080:1920" in f
    # Legacy ffmpeg centred-crop offsets for the scaled 3414x1920 frame:
    # x = (scaled_w - out_w) // 2 = (3414-1080)//2 = 1167
    # y = (scaled_h - out_h) // 2 = (1920-1920)//2 = 0
    x, y = _parse_crop_xy(f)
    assert x == 1167
    assert y == 0


def test_y_offset_is_centred_not_biased_for_tall_source():
    # A source proportionally taller than 9:16 (e.g. 720x1600) is the case
    # where a head-biased y (// 4) would diverge from centred y (// 2).
    # scale_f = max(1080/720, 1920/1600) = 1.5 -> scaled 1080x2400 (both even).
    # Centred y = (2400-1920)//2 = 240.
    f = render.cover_crop_filter(720, 1600, center_x=0.5)
    scaled_w, scaled_h = _parse_scale(f)
    assert (scaled_w, scaled_h) == (1080, 2400)
    _, y = _parse_crop_xy(f)
    assert y == 240


def test_off_centre_subject_shifts_crop_left():
    left = render.cover_crop_filter(1920, 1080, center_x=0.2)
    centre = render.cover_crop_filter(1920, 1080, center_x=0.5)
    x_left, _ = _parse_crop_xy(left)
    x_centre, _ = _parse_crop_xy(centre)
    assert x_left < x_centre


def test_crop_never_leaves_the_frame():
    for cx in (0.0, 0.01, 0.99, 1.0):
        f = render.cover_crop_filter(1920, 1080, center_x=cx)
        scaled_w, _ = _parse_scale(f)
        x, _ = _parse_crop_xy(f)
        assert x >= 0
        assert x + 1080 <= scaled_w


def test_draft_uses_smaller_canvas():
    assert "720:1280" in render.cover_crop_filter(1920, 1080, 0.5, draft=True)


def test_already_vertical_source_is_not_cropped_horizontally():
    f = render.cover_crop_filter(1080, 1920, center_x=0.5)
    x, _ = _parse_crop_xy(f)
    assert x == 0


def test_missing_dimensions_fall_back_to_legacy_safe_filter():
    # When source dimensions are unknown (e.g. ffprobe failed upstream),
    # cover_crop_filter must not emit concrete pixel numbers computed from a
    # guess — it must fall back to the aspect-safe, ffmpeg-native filter that
    # computes everything at run time from the real input.
    f = render.cover_crop_filter(0, 0, center_x=0.5)
    assert f == "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"


def test_missing_dimensions_fall_back_to_legacy_safe_filter_draft():
    f = render.cover_crop_filter(0, 0, center_x=0.5, draft=True)
    assert f == "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280"
