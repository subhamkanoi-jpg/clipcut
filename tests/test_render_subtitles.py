import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))

import render
from edl import default_subtitle_font, force_style


def test_subtitles_clause_includes_fontsdir_when_bundled_font_present(tmp_path: Path):
    import captions_ass

    subs = tmp_path / "master.srt"
    subs.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")

    style = force_style(font=default_subtitle_font())
    clause = render.subtitles_filter_clause(subs, style)

    assert clause.startswith("subtitles='")
    assert ":fontsdir='" in clause
    assert f"FontName={captions_ass.FONT}" in clause
    # fontsdir must come from the real, escaped bundled-font directory, not
    # a placeholder or the subtitles path itself.
    escaped_fonts_dir = str(captions_ass.FONTS_DIR.resolve()).replace("\\", "/").replace(":", r"\:")
    assert escaped_fonts_dir in clause


def test_subtitles_clause_omits_fontsdir_and_uses_fallback_when_font_missing(monkeypatch, tmp_path: Path):
    # Fix pass (review finding): simulate the bundled font being absent by
    # repointing captions_ass.FONT_FILE at a nonexistent path (never delete
    # the real bundled font). The produced filter must not reference
    # fontsdir at all, and the FontName it requests must be the fallback
    # family rather than the bundled one nothing would resolve.
    import captions_ass

    monkeypatch.setattr(captions_ass, "FONT_FILE", tmp_path / "nonexistent.ttf")
    assert captions_ass.font_available() is False

    subs = tmp_path / "master.srt"
    subs.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")

    style = force_style(font=default_subtitle_font())
    clause = render.subtitles_filter_clause(subs, style)

    assert "fontsdir=" not in clause
    assert f"FontName={captions_ass.FONT_FALLBACK}" in clause
    assert captions_ass.FONT not in clause
