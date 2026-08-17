"""Real end-to-end regression guard for build_final_composite's audio mapping.

`build_final_composite` used to hardcode `-map 0:a` on its filter-graph
ffmpeg pass, so any source with no audio stream (e.g. a silent screen
recording) made the whole composite fail outright instead of just producing
a silent output -- the old `burn_captions` used a bare `-c:a copy` and
tolerated this fine. These tests run real ffmpeg (not mocked) against a
genuinely silent fixture and a genuinely audible one, so they exercise the
actual `-map`/`-c:a` flags ffmpeg receives, not just the Python that builds
the command list.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))

import render


def _make_silent_video(path: Path, duration: float = 1.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"color=c=blue:s=320x240:d={duration}:r=24",
         "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )


def _make_audible_video(path: Path, duration: float = 1.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"color=c=blue:s=320x240:d={duration}:r=24",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         str(path)],
        check=True, capture_output=True,
    )


def _make_srt(path: Path) -> None:
    path.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")


def test_has_audio_stream_reflects_real_source(tmp_path):
    silent = tmp_path / "silent.mp4"
    audible = tmp_path / "audible.mp4"
    _make_silent_video(silent)
    _make_audible_video(audible)

    assert render.has_audio_stream(silent) is False
    assert render.has_audio_stream(audible) is True


def test_build_final_composite_tolerates_missing_audio_stream(tmp_path):
    # Force the filter-graph path (not the early `-c copy` shortcut) by
    # supplying subtitles, so this actually exercises the `-map 0:a` bug.
    base = tmp_path / "base.mp4"
    _make_silent_video(base)
    subs = tmp_path / "subs.srt"
    _make_srt(subs)

    out = tmp_path / "out.mp4"
    render.build_final_composite(base, [], subs, out, tmp_path)

    assert out.is_file()
    assert out.stat().st_size > 0
    assert render.has_audio_stream(out) is False


def test_build_final_composite_still_carries_audio_when_present(tmp_path):
    base = tmp_path / "base.mp4"
    _make_audible_video(base)
    subs = tmp_path / "subs.srt"
    _make_srt(subs)

    out = tmp_path / "out.mp4"
    render.build_final_composite(base, [], subs, out, tmp_path)

    assert out.is_file()
    assert out.stat().st_size > 0
    assert render.has_audio_stream(out) is True
