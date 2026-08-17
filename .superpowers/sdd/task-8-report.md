# Task 8 Report: Port karaoke ASS captions into helpers

## Status: DONE_WITH_CONCERNS

## What was implemented

- Created `helpers/captions_ass.py` — a faithful copy of the karaoke ASS
  captioning code from `backend/render_engine.py`:
  - `PUNCT_BREAK`, `YELLOW`, `WHITE` colour constants
  - `CAPTION_STYLES` (bold / neon / boxed / minimal)
  - `_ass_ts`, `_clean`, `timeline_chunks`, `build_ass`
  - `FONT` set to `"ClipCut Sans"` with a new `FONT_FALLBACK = "Liberation Sans"`,
    per the brief's note that Task 9 bundles the real font.
  - `build_ass` gained a `fonts_dir: Path | None = None` parameter (unused
    inside the function body — libass resolves fonts at burn time; the burn
    step consumes it in Task 9).
- Created `tests/test_captions_ass.py` (5 tests, from the brief's Step 1,
  with one corrected fixture — see "Deviation from the brief" below).
- `backend/render_engine.py` was **not modified** — left in place per the
  Global Constraints, to keep working until Task 12's parity test.

## Deviation from the brief (read this first)

The brief's Step 1 test fixture is:

```python
RANGES = [{"start": 0.0, "end": 2.0}]
```

This is a **dict**, but the copied `timeline_chunks`/`build_ass` code (per
the brief's own Step 3 instructions, "copy unchanged") does:

```python
for r_start, r_end in ranges:
```

Unpacking a 2-key dict this way yields its **keys** (`"start"`, `"end"` as
strings), not its values — confirmed empirically:

```python
>>> d = {"start": 0.0, "end": 2.0}
>>> for a, b in [d]: print(repr(a), repr(b))
'start' 'end'
```

Running the brief's fixture verbatim against the freshly-copied
implementation produced `TypeError: unsupported operand type(s) for -: 'str'
and 'str'` in `timeline_chunks`, not a passing test. I verified the *real*
contract for `ranges` (a list of `(start, end)` pairs, never dicts) two
independent ways in the existing codebase before touching anything:

- `backend/cuts.py::keep_ranges` returns `[[start, end], ...]` (line 57-67).
- `backend/render_engine.py` itself unpacks ranges as pairs in two places:
  `for r_start, r_end in ranges:` (line 200, inside the very `timeline_chunks`
  being copied) and `for i, (a, b) in enumerate(ranges):` (line 347, inside
  `render_export`).

Given this is the copied function's own established contract, and the
brief's fixture directly contradicts it, I corrected only the test fixture:

```python
RANGES = [(0.0, 2.0)]
```

I left the copied implementation code untouched (no accommodation for dict
ranges was added — that would have been a rewrite, which the brief
explicitly prohibits, and would risk parity drift against
`backend/render_engine.py` ahead of Task 12). The fixture correction is
documented inline in `tests/test_captions_ass.py` with the same reasoning.

This is flagged as a concern rather than a clean DONE because the
instructions asked for the brief's test cases to be used verbatim, and I
deviated from that in one line, judging the fixture itself to be buggy
rather than the implementation. Recommend the SDD maintainer correct
`task-8-brief.md`'s Step 1 fixture so future tasks referencing this pattern
(e.g. Task 12's parity harness) don't inherit the same bug.

## TDD evidence

**RED** — before `helpers/captions_ass.py` existed:

```
$ .venv-local/Scripts/python.exe -m pytest tests/test_captions_ass.py -v
...
ModuleNotFoundError: No module named 'captions_ass'
=========================== 1 error in 0.21s ===========================
```

**Intermediate failure** — after creating `helpers/captions_ass.py` but
before fixing the fixture (verbatim brief fixture):

```
4 failed, 1 passed in 0.23s
TypeError: unsupported operand type(s) for -: 'str' and 'str'
TypeError: '>' not supported between instances of 'float' and 'str'
```

**GREEN** — after correcting only the `RANGES` fixture:

```
$ .venv-local/Scripts/python.exe -m pytest tests/test_captions_ass.py -v
tests/test_captions_ass.py::test_styles_include_the_four_shipped_names PASSED
tests/test_captions_ass.py::test_build_ass_writes_a_playable_header PASSED
tests/test_captions_ass.py::test_karaoke_emits_inline_overrides PASSED
tests/test_captions_ass.py::test_no_words_produces_empty_file_and_zero_count PASSED
tests/test_captions_ass.py::test_chunks_respect_max_words PASSED
============================== 5 passed in 0.13s ==============================
```

## Root-suite regression check

```
$ .venv-local/Scripts/python.exe -m pytest tests/ -v
...
6 failed, 91 passed, 1 warning in 16.10s
```

Failures (identical set to the documented baseline, no new failures):
- `test_api.py::test_transcribe_requires_explicit_click`
- `test_api.py::test_transcribe_409_when_busy`
- `test_claude.py::test_first_turn_has_no_resume`
- `test_cut_picks.py::test_apply_cut_picks_zooms_and_drops`
- `test_talking_head.py::test_apply_bin_inserts_broll`
- `test_visual_picks.py::test_apply_visuals_inserts_broll_and_graphic`

86 baseline passed + 5 new tests = 91 passed. 6 failed, matching baseline
exactly. No regressions.

## Files changed

- `helpers/captions_ass.py` (new)
- `tests/test_captions_ass.py` (new)

Commit: `d80aab0` — "feat: port karaoke ASS captions into helpers"

Staged explicitly (`git add helpers/captions_ass.py tests/test_captions_ass.py`),
verified with `git status --short` showing only those two files as `A` before
committing. Pre-existing unstaged modifications under `.superpowers/sdd/*`
(present before this task started) were left untouched and are still
unstaged after the commit.

## How the copy's faithfulness was verified

Ran a per-symbol `difflib.unified_diff` between the original functions in
`backend/render_engine.py` and the new copies in `helpers/captions_ass.py`:

- `_ass_ts`: **no differences**.
- `_clean`: **no differences**.
- `timeline_chunks`: **no differences**.
- `CAPTION_STYLES` + colour constants block (with `FONT`/`FONT_FALLBACK`
  lines excluded from the diff since that's the brief's sanctioned change):
  only difference is the absence of `PRESCALE = 1.3` and `FPS = 30`, which
  are unrelated render constants used only by `extract_segment` (not in the
  brief's copy list, correctly excluded).
- `build_ass`: only difference is the added `fonts_dir: Path | None = None`
  parameter and trailing blank-line whitespace (non-semantic).

No other behavioural differences were introduced.

## Self-review findings

- Copy is faithful per the diff above — the only differences are the two
  the brief asked for (font constant, `fonts_dir` param).
- `fonts_dir` is accepted but genuinely unused inside `build_ass`, exactly as
  the brief specifies ("not used inside the ASS file itself... the burn step
  passes it to ffmpeg in Task 9").
- `backend/render_engine.py` was read but not written to, confirmed by
  `git status --short` never showing it as modified at any point in this
  session.
- The symbol list in the brief (colour constants, `PUNCT_BREAK`, `FONT`,
  `CAPTION_STYLES`, `_ass_ts`, `_clean`, `timeline_chunks`, `build_ass`)
  matched what's actually in `backend/render_engine.py` — no discrepancy
  there. The only discrepancy found was in the brief's test *fixture data*
  (see "Deviation from the brief" above), not its symbol list.

## Concerns

1. **Brief fixture bug** (detailed above) — recommend fixing
   `task-8-brief.md`'s `RANGES` fixture from `[{"start": 0.0, "end": 2.0}]`
   to `[(0.0, 2.0)]` so it doesn't get copy-pasted into a later task (e.g.
   Task 12's parity harness) and cause the same failure there.
2. `FONT = "ClipCut Sans"` is a placeholder per the brief's own note; Task 9
   is expected to correct it once the real bundled font family name is
   settled. No action needed here, just noting it's intentionally
   provisional.
