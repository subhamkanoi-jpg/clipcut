from __future__ import annotations

import sys
from pathlib import Path

import pytest

from edl import default_subtitle_font, escape_subtitles_path, force_style, validate_edl


def _edl(tmp: Path, **over):
    src = tmp / "C0103.MP4"
    src.write_bytes(b"x")
    base = {
        "version": 1,
        "sources": {"C0103": str(src)},
        "ranges": [{"source": "C0103", "start": 2.42, "end": 6.85, "beat": "HOOK", "quote": "x", "reason": "y"}],
        "grade": "none",
        "overlays": [],
        "total_duration_s": 4.43,
    }
    base.update(over)
    return base


def test_valid(tmp_path: Path):
    result = validate_edl(_edl(tmp_path), edit_dir=tmp_path)
    assert result.ok
    assert result.errors == []


def test_unknown_source(tmp_path: Path):
    edl = _edl(tmp_path)
    edl["ranges"][0]["source"] = "NOPE"
    result = validate_edl(edl, edit_dir=tmp_path)
    assert not result.ok
    assert any("NOPE" in e for e in result.errors)


def test_missing_source_file(tmp_path: Path):
    edl = _edl(tmp_path)
    edl["sources"]["C0103"] = str(tmp_path / "missing.mp4")
    result = validate_edl(edl, edit_dir=tmp_path)
    assert not result.ok


def test_start_not_less_than_end(tmp_path: Path):
    edl = _edl(tmp_path)
    edl["ranges"][0]["start"] = 6.85
    edl["ranges"][0]["end"] = 2.42
    result = validate_edl(edl, edit_dir=tmp_path)
    assert not result.ok


def test_total_duration_autocorrect(tmp_path: Path):
    edl = _edl(tmp_path, total_duration_s=99.0)
    result = validate_edl(edl, edit_dir=tmp_path)
    assert result.ok
    assert result.warnings
    assert result.edl["total_duration_s"] == pytest.approx(4.43, abs=0.001)


def test_missing_overlay_file(tmp_path: Path):
    edl = _edl(tmp_path, overlays=[{"file": "animations/slot_1/render.mp4", "start_in_output": 0, "duration": 5}])
    result = validate_edl(edl, edit_dir=tmp_path)
    assert not result.ok


def test_escape_windows_drive():
    escaped = escape_subtitles_path(Path(r"C:\Users\Varun B\takes\edit\master.srt"))
    assert escaped.startswith("C\\:")
    assert ":" not in escaped.replace("\\:", "")
    assert "Varun B" in escaped or "Varun\\ B" in escaped
    assert "master.srt" in escaped


def test_default_font_uses_bundled_family(monkeypatch):
    # Task 9: font choice is deterministic (the bundled family), not a
    # platform guess (previously "Arial" on win32 / "Helvetica" elsewhere).
    import captions_ass
    monkeypatch.setattr(sys, "platform", "win32")
    assert default_subtitle_font() == captions_ass.FONT
    monkeypatch.setattr(sys, "platform", "darwin")
    assert default_subtitle_font() == captions_ass.FONT


def test_default_font_falls_back_when_captions_ass_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "captions_ass", None)
    assert default_subtitle_font() == "Liberation Sans"


def test_force_style_uses_font():
    style = force_style(font="Arial")
    assert "FontName=Arial" in style
    assert "MarginV=90" in style
