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


def test_subtitles_clause_omits_force_style_for_ass_but_keeps_fontsdir(tmp_path: Path):
    # ASS files (helpers/captions_ass.py::build_ass) carry a fully computed
    # [V4+ Styles] line for the target resolution -- correct FontSize,
    # MarginV, colours, etc. libass's force_style overrides style lines
    # defined *inside* the subtitle file, so applying it here would stomp
    # that computed styling (this was the regression: FontSize 105 -> 18,
    # MarginV 288 -> 90). fontsdir must still be present -- it only affects
    # font lookup, not styling, so it is orthogonal to force_style.
    subs = tmp_path / "captions.ass"
    subs.write_text(
        "[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Style: Cap,DejaVu Sans,105,&H0000FFD4,&H0000FFD4,&H00000000,&H90000000,"
        "-1,0,0,0,100,100,0,0,1,6.0,1.8,2,86,86,288,1\n\n"
        "[Events]\n"
    )

    style = force_style(font=default_subtitle_font())
    clause = render.subtitles_filter_clause(subs, style)

    assert ":fontsdir='" in clause
    assert "force_style" not in clause


def test_subtitles_clause_keeps_force_style_and_fontsdir_for_srt(tmp_path: Path):
    # SRT files carry no styling of their own -- force_style is what gives
    # them any style at all, so it must still be applied here.
    subs = tmp_path / "master.srt"
    subs.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")

    style = force_style(font=default_subtitle_font())
    clause = render.subtitles_filter_clause(subs, style)

    assert ":fontsdir='" in clause
    assert "force_style" in clause
    assert f":force_style='{style}'" in clause


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
