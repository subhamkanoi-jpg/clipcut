"""Regression guard for the animated-zoom port (ClipCut Finding 1 / Finding 3).

Fast, no real ffmpeg process: monkeypatches `render.run_ffmpeg` to capture the
built command instead of running it, then inspects the `-vf` filter chain and
`-r` frame-rate flag. This is what would have caught the original regression
(the zoom path silently going dead, and the frame rate silently dropping
30 -> 24) without needing a real render — see
backend/tests/test_render_parity.py for the real end-to-end render that
exercises the same code with actual ffmpeg.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))

import render


def _captured_vf(monkeypatch, tmp_path, **kwargs) -> str:
    captured = {}

    def fake_run_ffmpeg(cmd):
        captured["cmd"] = cmd

    monkeypatch.setattr(render, "run_ffmpeg", fake_run_ffmpeg)
    render.extract_segment(
        tmp_path / "nonexistent_source.mp4",
        0.0, 2.0, "", tmp_path / "out.mp4",
        **kwargs,
    )
    cmd = captured["cmd"]
    return cmd[cmd.index("-vf") + 1]


def _captured_cmd(monkeypatch, tmp_path, **kwargs) -> list:
    captured = {}

    def fake_run_ffmpeg(cmd):
        captured["cmd"] = cmd

    monkeypatch.setattr(render, "run_ffmpeg", fake_run_ffmpeg)
    render.extract_segment(
        tmp_path / "nonexistent_source.mp4",
        0.0, 2.0, "", tmp_path / "out.mp4",
        **kwargs,
    )
    return captured["cmd"]


def test_zoompan_present_when_move_has_a_zoom_ramp(tmp_path, monkeypatch):
    move = {"kind": "push in", "z0": 1.0, "z1": 1.12, "snaps": []}
    vf = _captured_vf(monkeypatch, tmp_path, move=move)
    assert "zoompan=" in vf


def test_zoompan_present_when_move_is_snaps_only(tmp_path, monkeypatch):
    # A "hold" (z0 == z1 == 1.0) with a punch-in snap must still animate --
    # the snap alone is enough to make the segment "zooming".
    move = {
        "kind": "hold", "z0": 1.0, "z1": 1.0,
        "snaps": [{"t": 0.2, "amp": 0.08, "decay": 0.38, "word": "never"}],
    }
    vf = _captured_vf(monkeypatch, tmp_path, move=move)
    assert "zoompan=" in vf


def test_no_zoompan_when_move_is_none(tmp_path, monkeypatch):
    vf = _captured_vf(monkeypatch, tmp_path, move=None)
    assert "zoompan=" not in vf


def test_no_zoompan_when_move_is_flat_hold_without_snaps(tmp_path, monkeypatch):
    # kind == "hold", z0 == z1 == 1.0, no snaps -- exactly what
    # backend/zooms.py::plan emits for a hold segment with punch_ins off.
    # Must fall back to the plain static-scale path, not zoompan.
    move = {"kind": "hold", "z0": 1.0, "z1": 1.0, "snaps": []}
    vf = _captured_vf(monkeypatch, tmp_path, move=move)
    assert "zoompan=" not in vf


def test_zoompan_coexists_with_cover_crop(tmp_path, monkeypatch):
    move = {"kind": "push in", "z0": 1.0, "z1": 1.1, "snaps": []}
    vf = _captured_vf(monkeypatch, tmp_path, move=move, cover=True, center_x=0.3)
    assert "zoompan=" in vf
    # cover mode still crops (to the prescaled canvas) ahead of the zoompan.
    assert "crop=" in vf


def test_static_zoom_path_unchanged_when_move_is_none(tmp_path, monkeypatch):
    vf = _captured_vf(monkeypatch, tmp_path, move=None, zoom=1.2)
    assert "zoompan=" not in vf
    assert "crop=trunc(iw/1.200/2)*2:trunc(ih/1.200/2)*2" in vf


def test_move_none_output_matches_pre_move_baseline(tmp_path, monkeypatch):
    """Locks in that adding the move/fps parameters left the default call's
    filter chain byte-for-byte identical to what it was before those
    parameters existed: unzoomed, uncropped, ungraded -> just the plain
    landscape scale."""
    vf = _captured_vf(monkeypatch, tmp_path, move=None)
    assert vf == "scale=1920:-2"


def test_fps_defaults_to_24_for_backward_compatibility(tmp_path, monkeypatch):
    cmd = _captured_cmd(monkeypatch, tmp_path, move=None)
    assert cmd[cmd.index("-r") + 1] == "24"


def test_fps_can_be_overridden(tmp_path, monkeypatch):
    cmd = _captured_cmd(monkeypatch, tmp_path, move=None, fps=30)
    assert cmd[cmd.index("-r") + 1] == "30"


def test_zoompan_fps_matches_requested_fps(tmp_path, monkeypatch):
    move = {"kind": "push in", "z0": 1.0, "z1": 1.1, "snaps": []}
    vf = _captured_vf(monkeypatch, tmp_path, move=move, fps=30)
    assert ":fps=30" in vf
    assert "fps=30," in vf  # the explicit fps= filter stage before zoompan
