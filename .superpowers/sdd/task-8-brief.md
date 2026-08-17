### Task 8: Port karaoke ASS captions into helpers

**Files:**
- Create: `helpers/captions_ass.py`
- Create: `tests/test_captions_ass.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `helpers.captions_ass.CAPTION_STYLES: dict` (moved verbatim from `backend/render_engine.py:23-47`); `helpers.captions_ass.timeline_chunks(words, ranges, max_words=3) -> list`; `helpers.captions_ass.build_ass(words, ranges, out_path, style, width, height, karaoke=True, fonts_dir=None) -> int` returning the number of caption events written.

This is a move, not a rewrite. Copy `timeline_chunks`, `build_ass`, `_ass_ts`,
`_clean`, `CAPTION_STYLES`, and the colour constants from
`backend/render_engine.py` unchanged, then add the `fonts_dir` parameter.

**Transitional duplication is expected here.** From this task until Task 12, this
logic exists in both `helpers/captions_ass.py` and `backend/render_engine.py`.
That is deliberate: Task 12's parity test renders through *both* renderers and
compares them, which requires both to still exist. The duplicate is deleted in
Task 12 Step 5 along with the rest of `render_engine.py`. Do not try to resolve
it earlier by having one import from the other — that would couple the module
being deleted to the module replacing it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_captions_ass.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))

import captions_ass


WORDS = [
    {"text": "hello", "start": 0.0, "end": 0.4, "type": "word"},
    {"text": "there", "start": 0.4, "end": 0.9, "type": "word"},
    {"text": "friend", "start": 1.0, "end": 1.6, "type": "word"},
]
RANGES = [{"start": 0.0, "end": 2.0}]


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../.venv-local/Scripts/python.exe -m pytest tests/test_captions_ass.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'captions_ass'`

- [ ] **Step 3: Write minimal implementation**

Create `helpers/captions_ass.py` by copying these symbols verbatim from
`backend/render_engine.py`: the colour constants (`YELLOW`, `WHITE`), `PUNCT_BREAK`,
`FONT`, `CAPTION_STYLES` (lines 14-47), `_ass_ts` (182-190), `_clean` (191-194),
`timeline_chunks` (195-229), and `build_ass` (230-297).

Then make two changes to the copy:

```python
# At the top, replace the hardcoded font default:
FONT = "ClipCut Sans"          # bundled; see assets/fonts/ (Task 9)
FONT_FALLBACK = "Liberation Sans"
```

```python
# Add fonts_dir to the signature and record it for the caller:
def build_ass(words: list, ranges: list, out_path: Path, style: dict,
              width: int, height: int, karaoke: bool = True,
              fonts_dir: Path | None = None) -> int:
```

`fonts_dir` is not used inside the ASS file itself — libass resolves fonts at
burn time — but accepting it here keeps the caller's call-site uniform and
documents the dependency. The burn step passes it to ffmpeg in Task 9.

- [ ] **Step 4: Run test to verify it passes**

Run: `../.venv-local/Scripts/python.exe -m pytest tests/test_captions_ass.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add helpers/captions_ass.py tests/test_captions_ass.py
git commit -m "feat: port karaoke ASS captions into helpers"
```

---

