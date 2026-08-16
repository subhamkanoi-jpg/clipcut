import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))

import captions_ass


WORDS = [
    {"text": "hello", "start": 0.0, "end": 0.4, "type": "word"},
    {"text": "there", "start": 0.4, "end": 0.9, "type": "word"},
    {"text": "friend", "start": 1.0, "end": 1.6, "type": "word"},
]
# NOTE: ranges are (start, end) pairs, matching the real contract used
# throughout the codebase (see backend/cuts.py::keep_ranges and
# backend/render_engine.py's `for r_start, r_end in ranges` /
# `for i, (a, b) in enumerate(ranges)`). The task-8 brief's Step 1 fixture
# used `[{"start": 0.0, "end": 2.0}]` (a dict), which breaks tuple-unpacking
# in the copied `timeline_chunks`/`build_ass` code (unpacking a 2-key dict
# yields its *keys*, not its values) — confirmed empirically, see task-8
# report for detail. Corrected here to a (start, end) tuple.
RANGES = [(0.0, 2.0)]


def test_styles_include_the_four_shipped_names():
    assert set(captions_ass.CAPTION_STYLES) == {"bold", "neon", "boxed", "minimal"}


def test_build_ass_writes_a_playable_header(tmp_path):
    out = tmp_path / "subs.ass"
    n = captions_ass.build_ass(
        WORDS, RANGES, out, captions_ass.CAPTION_STYLES["bold"], 1080, 1920,
    )
    text = out.read_text(encoding="utf-8")
    assert n > 0
    assert "[Script Info]" in text
    assert "PlayResX: 1080" in text
    assert "PlayResY: 1920" in text
    assert "Dialogue:" in text


def test_karaoke_emits_inline_overrides(tmp_path):
    out = tmp_path / "k.ass"
    captions_ass.build_ass(
        WORDS, RANGES, out, captions_ass.CAPTION_STYLES["neon"], 1080, 1920,
        karaoke=True,
    )
    assert "\\c&H" in out.read_text(encoding="utf-8")


def test_no_words_produces_empty_file_and_zero_count(tmp_path):
    out = tmp_path / "empty.ass"
    assert captions_ass.build_ass([], RANGES, out,
                                  captions_ass.CAPTION_STYLES["bold"],
                                  1080, 1920) == 0
    assert out.read_text(encoding="utf-8") == ""


def test_chunks_respect_max_words():
    chunks = captions_ass.timeline_chunks(WORDS, RANGES, max_words=2)
    assert all(len(c["words"]) <= 2 for c in chunks)
