# Task 7 Report: Face-centered crop in the helpers renderer

## Status: DONE_WITH_CONCERNS

## What was implemented

- `helpers/render.py`: new pure helper `cover_crop_filter(src_w, src_h, center_x=0.5, draft=False) -> str`
  above `extract_segment`. It scales the source so both dimensions cover the
  target canvas (1080x1920, or 720x1280 in draft mode), then computes a
  clamped crop `x` offset so the window containing `center_x` (a 0.0-1.0
  fraction of source width) never runs past either edge of the scaled frame.
  Returns the `scale=...,crop=...:x=...:y=...` ffmpeg filter chain.
- `extract_segment()` gained `center_x: float = 0.5` after the existing
  `cover: bool = False` parameter. The `cover` branch now probes the source's
  actual pixel size (falling back to `(1920, 1080)` if probing fails) and
  calls `cover_crop_filter(size[0], size[1], center_x, draft=draft)` instead
  of the old fixed `scale=W:H:force_original_aspect_ratio=increase,crop=W:H`
  string (which always cropped dead centre with no `x`/`y` control).
- `tests/test_render_cover.py` (new, repo-root `tests/`): 5 tests per the
  brief, puts `helpers/` on `sys.path` and imports `render` flatly.

## TDD evidence

**RED** — `.venv-local/Scripts/python.exe -m pytest tests/test_render_cover.py -v`
(after writing the test file, before touching render.py):
```
tests/test_render_cover.py::test_centered_crop_matches_legacy_behaviour FAILED
tests/test_render_cover.py::test_off_centre_subject_shifts_crop_left FAILED
tests/test_render_cover.py::test_crop_never_leaves_the_frame FAILED
tests/test_render_cover.py::test_draft_uses_smaller_canvas FAILED
tests/test_render_cover.py::test_already_vertical_source_is_not_cropped_horizontally FAILED
AttributeError: module 'render' has no attribute 'cover_crop_filter'
5 failed in 0.17s
```

**GREEN (first pass, after implementing exactly the brief's Step 3 code)** —
same command:
```
test_centered_crop_matches_legacy_behaviour PASSED
test_off_centre_subject_shifts_crop_left PASSED
test_crop_never_leaves_the_frame FAILED  -> assert (2334 + 1080) <= 1920
test_draft_uses_smaller_canvas PASSED
test_already_vertical_source_is_not_cropped_horizontally PASSED
4 passed, 1 failed
```

**Root-caused and fixed the test** (see "Deviation from brief" below), then
**GREEN (final)** — `.venv-local/Scripts/python.exe -m pytest tests/test_render_cover.py -v`:
```
tests/test_render_cover.py::test_centered_crop_matches_legacy_behaviour PASSED
tests/test_render_cover.py::test_off_centre_subject_shifts_crop_left PASSED
tests/test_render_cover.py::test_crop_never_leaves_the_frame PASSED
tests/test_render_cover.py::test_draft_uses_smaller_canvas PASSED
tests/test_render_cover.py::test_already_vertical_source_is_not_cropped_horizontally PASSED
5 passed in 0.04s
```

## Deviation from brief: fixed a bug in `test_crop_never_leaves_the_frame`

The brief's test (copied verbatim first) asserted, for a 1920x1080 source
being cover-cropped to a 1080x1920 canvas:
```python
x = int(f.split(":x=")[1].split(":")[0])
assert x >= 0
assert x + 1080 <= 1920
```
`x` is a coordinate in the **scaled** canvas that `cover_crop_filter` produces
(the `crop` filter runs after `scale` in the chain, so its `x`/`y` are pixel
offsets into the scaled image, not the original source). For a 16:9 source
covering a 9:16 target, the scale-to-cover step must scale width up to
`1920 * (1920/1080) ≈ 3413.3px → 3414px` (matching the *legacy* filter's own
`force_original_aspect_ratio=increase` semantics — this is not new to my
change, verified via `test_centered_crop_matches_legacy_behaviour` passing
at the default `center_x=0.5`, which reproduces the exact legacy centre
offset of 1167px). The crop window's valid `x` range is therefore
`[0, 3414-1080] = [0, 2334]`, and at `center_x` near 1.0 the correct,
in-frame answer is `x=2334`, giving `x + 1080 = 3414`. Comparing that against
the literal source width `1920` is mathematically unsatisfiable for **any**
correct cover-crop implementation of this source/target pair — it isn't an
implementation choice.

I verified this by hand-computing the values (`scale_f=1.7778`,
`scaled_w=3414`), confirmed the test failure reproduced that exact number,
and confirmed no alternate formula could satisfy both this bound and the
`crop=1080:1920` requirement from test 1 simultaneously (the crop width is
fixed at 1080 and the source must be scaled up to at least 1920px height to
cover, which mechanically forces `scaled_w=3414` for this specific
1920x1080 → 1080x1920 case).

I fixed only this one assertion to check the real invariant — the crop
window stays inside the **scaled** frame — by recomputing `scaled_w` in the
test the same way the implementation derives it, rather than hardcoding an
impossible bound. No other test, and no implementation code, was changed to
make this pass. See `tests/test_render_cover.py::test_crop_never_leaves_the_frame`
for the corrected version and inline comment explaining why.

## Root `tests/` regression run (Step 5)

Command: `.venv-local/Scripts/python.exe -m pytest tests/ --ignore=tests/test_api.py -v`

Result: **69 passed, 4 failed** (73 collected; up from 68 total before this
task's 5 new tests were added).

`tests/test_api.py` fails to even *collect* on this machine
(`ModuleNotFoundError: No module named 'httpx'`) — pre-existing, unrelated to
`helpers/`, and out of scope (Global Constraints forbid adding dependencies).

The 4 failures are pre-existing and unrelated to this change. Verified by
`git stash`-ing `helpers/render.py` only (keeping the new test file aside)
and re-running the full suite on the untouched baseline: **the same 4 tests
fail identically** with the same error text, with 64 passed (69 minus the 5
new tests). Then restored my changes and re-ran: 69 passed, same 4 failures.

The 4 pre-existing failures:
1. `tests/test_claude.py::test_first_turn_has_no_resume` — asserts
   `cmd[0] == "claude"` but gets the full resolved `claude.exe` path on this
   machine; environment/PATH-resolution issue, nothing to do with `render.py`.
2. `tests/test_cut_picks.py::test_apply_cut_picks_zooms_and_drops` — asserts
   `out[1]["zoom"] == 1.35`, gets `1.28`; lives entirely in the unrelated
   `cut_picks` module.
3. `tests/test_talking_head.py::test_apply_bin_inserts_broll` —
   `ModuleNotFoundError: No module named 'PIL'` inside `helpers/graphics.py`;
   missing dependency, unrelated to render/crop.
4. `tests/test_visual_picks.py::test_apply_visuals_inserts_broll_and_graphic` —
   same missing-`PIL` root cause via `helpers/visual_picks.py`.

None of these touch `render.py`, `extract_segment`, or `cover_crop_filter`.

## Files changed

- `D:\Desktop\Desktop Files\Projects\clipcut\helpers\render.py` — added
  `cover_crop_filter()`; `extract_segment()` gained `center_x: float = 0.5`
  and its `cover` branch now calls the new helper.
- `D:\Desktop\Desktop Files\Projects\clipcut\tests\test_render_cover.py` —
  new file, 5 tests (one assertion corrected from the brief's verbatim text,
  see above).

## Self-review

- **Crop arithmetic correctness**: verified programmatically (not just via
  the 5 given tests) by sweeping `center_x` from 0.0 to 1.0 in steps of 0.01
  across six src/target aspect combinations — 1920x1080, 1080x1920 (exact
  match), 1280x720, 3840x2160, 1x1, and an extreme 100x1 — and confirming
  `0 <= x` and `x + out_w <= scaled_w` (and the analogous check for `y`)
  holds at every single sample, zero violations in all 606 checks. The crop
  window never leaves the scaled frame at any `center_x` in `[0, 1]`.
- **Default identical to previous behaviour (x-axis)**: confirmed
  `cover_crop_filter(1920, 1080)` (default `center_x=0.5`) produces
  `x=1167`, which equals `(scaled_w - out_w) / 2 = (3414-1080)/2 = 1167`
  exactly — the same centred offset ffmpeg's bare `crop=1080:1920` (no
  explicit `x`) would have computed under the old code. Also confirmed
  `cover_crop_filter(1920,1080)` (implicit default) and
  `cover_crop_filter(1920,1080,0.5)` (explicit default) produce byte-identical
  output.
- **Concern — y-axis is not byte-identical to legacy in one narrow case**:
  the brief's Step 3 snippet sets `y = max(0, (scaled_h - out_h) // 4)`
  ("bias upward; heads sit high in frame") rather than the legacy centred
  `(scaled_h - out_h) / 2`. For the primary use case this task targets
  (landscape source → 9:16 canvas, e.g. 1920x1080), `scaled_h` always equals
  `out_h` exactly (the height dimension is what's scaled-to-cover), so
  `y=0` either way and there is no observable difference — confirmed in the
  sweep above (`1920x1080 -> scaled 3414 1920`, i.e. no vertical excess).
  However, for a **portrait source that is proportionally taller than 9:16**
  under `cover=True` (e.g. a 720x1600 clip, aspect narrower than 1080:1920),
  `scaled_h` does exceed `out_h`, and the new upward-biased `y` differs from
  the old centred `y` (e.g. 120 vs. legacy's 240 in that example). This is a
  real, if narrow, divergence from "identical to previous behaviour" for
  that input shape. I left it as the brief specified — the inline comment
  ("heads sit high in frame") reads as an intentional framing choice rather
  than an accidental regression, and it's outside what the given tests
  exercise — but flagging it explicitly per the self-review instruction to
  verify the default is "genuinely identical" to prior behaviour. If strict
  byte-identical default behaviour on the y-axis is required, `y` should be
  `(scaled_h - out_h) // 2` instead of `// 4`; I did not make that change
  unilaterally since it wasn't the subject of the request (this task's
  purpose is horizontal/face-centering) and the brief's code was explicit.
- **No new dependencies, no package-relative imports**: confirmed — the new
  code uses only `int`/`round`/`max`/`min`, and `render.py`'s existing flat
  `from edl import ...`-style imports are untouched.
- **Callers unaffected**: `extract_all_segments()` (the only in-repo caller
  of `extract_segment`) does not pass `center_x`, so it uses the new default
  of `0.5` — behaviourally unchanged. `backend/render_engine.py` also
  references `extract_segment`/`cover`, but per this task's scope
  (helpers-only, backend untouched) I did not modify or test it; flagging
  for whichever task wires OpenCV face detection through to `center_x` that
  the backend call site will need updating to actually pass a non-default
  `center_x`.

## Concerns

1. The y-axis "bias upward" behaviour (see self-review above) is not
   strictly identical to the pre-existing centred-crop behaviour for
   overly-tall portrait sources under `cover=True`. Narrow edge case, likely
   intentional, not fixed unilaterally.
2. One assertion in the brief's own `tests/test_render_cover.py` (as given
   verbatim in the task brief) was mathematically unsatisfiable and has been
   corrected — see "Deviation from brief" above. No implementation code was
   changed to accommodate this; only the flawed test assertion was fixed.
3. `tests/test_api.py` cannot be collected on this machine (missing
   `httpx`) — pre-existing environment gap, unrelated to this task, not
   fixed (Global Constraints forbid new dependencies). The regression run
   therefore used `--ignore=tests/test_api.py`; without that flag,
   `pytest tests/ -v` aborts at collection with `Interrupted: 1 error during
   collection` before running anything, which would also have happened on
   the pre-existing baseline.

## Fix pass

Addressed a review of the above implementation. All three findings originated
in the plan's own snippet, not in the choices made above. Fixed all three plus
one minor test-quality issue, in `helpers/render.py` and
`tests/test_render_cover.py` only.

### Finding 1 — y-bias broke backwards compatibility (Important)

`cover_crop_filter`'s `y` offset used `(scaled_h - out_h) // 4` with a "bias
upward; heads sit high in frame" comment. Legacy centred behaviour is `// 2`.
Changed the divisor to `// 2` and removed the comment (the biasing behaviour
it described no longer exists). No new parameter was added — per the fix
brief, head-biased framing is out of scope here and would need a deliberate
future design.

Before:
```python
y = max(0, (scaled_h - out_h) // 4)  # bias upward; heads sit high in frame
```
After:
```python
y = max(0, (scaled_h - out_h) // 2)
```

Verified the divergence this fixes, and that it's now gone, with a direct
Python check (see "Numbers verified" below): for a 720x1600 source (taller
than 9:16 — the case where scaled_h exceeds out_h), the old `// 4` gave
`y=120`; the corrected `// 2` gives `y=240`, matching legacy centred-crop
semantics. For the primary 1920x1080-source case, `scaled_h == out_h` exactly
so `y=0` under either divisor — this is why the divergence only shows up for
sources proportionally taller than 9:16, and why the "Also fix" test below
needed a second source shape to actually exercise the fixed line.

### Finding 2 — probe-failure fallback could distort the picture (Important)

Two changes, both required:

**(a) `cover_crop_filter`'s own `not src_w or not src_h` guard.** It returned
`scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h}:x=0:y=0`
— concrete top-left crop coordinates bolted onto an otherwise proportional
filter (harmless together since scale-to-cover onto an exact-match canvas
needs no offset, but semantically the wrong shape of fix). Changed to return
the true legacy string with no `x=`/`y=` at all, letting ffmpeg's own default
crop-centring apply:
```python
return f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h}"
```

**(b) `extract_segment`'s caller-side fallback.** It did
`size = probe_video_size(source) or (1920, 1080)` and always fed concrete
`(w, h)` into `cover_crop_filter`, which — since that function has no
`force_original_aspect_ratio` keyword in its concrete-numbers path — meant
a failed probe on a non-16:9 source produced a `scale=1920:1080`-shaped
filter that stretches the frame instead of covering it. Changed to pass
`(0, 0)` through when the probe fails, so `cover_crop_filter`'s own guard
(fixed in 2a) kicks in and returns the aspect-safe run-time-computed filter
instead of Python-side guessed pixel numbers:
```python
size = probe_video_size(source)
src_w, src_h = size if size else (0, 0)
scale = cover_crop_filter(src_w, src_h, center_x, draft=draft)
```

### Finding 3 — crop-bounds test was tautological (Important)

`test_crop_never_leaves_the_frame` recomputed `scaled_w` with the same
formula the production code uses, so a regression in the scale math would
silently move the test's own expected bound in lockstep and never fail.
Added an independent oracle: `_parse_scale()` / `_parse_crop_xy()` regex-parse
the real `scale=W:H` and `crop=...:x=..:y=..` values straight out of the
returned filter string, with no formula duplicated from the implementation.
Added `test_scale_dimensions_match_known_case`, which hardcodes and verifies
`scale=3414:1920` for a 1920x1080 source targeting 1080x1920 — verified by
direct computation before hardcoding (see "Numbers verified" below);
matches the number the original Task 7 report had already hand-derived
(`scale_f = 1920/1080 = 1.7778`, `scaled_w = round(1920*1.7778) = 3413` →
even-adjusted to `3414`). `test_crop_never_leaves_the_frame` now sweeps
`center_x` and asserts against the *parsed* `scaled_w`, not a recomputed one.

### Also fixed (Minor) — `test_centered_crop_matches_legacy_behaviour` asserted nothing numeric

It only checked `"crop=1080:1920" in f` and `":x=" in f`, both true by
construction regardless of the actual offset. Strengthened it to assert the
concrete parsed `x == 1167` and `y == 0` for the 1920x1080 known case.
Because that case has `scaled_h == out_h` (so `y` is 0 under either the old
or new divisor, and can't by itself catch a Finding-1 regression), also added
`test_y_offset_is_centred_not_biased_for_tall_source` using a 720x1600 source
(`scaled_h=2400 > out_h=1920`), asserting `y == 240` — this is the test that
actually pins the Finding 1 fix. Also added
`test_missing_dimensions_fall_back_to_legacy_safe_filter` (and a `draft=True`
variant) asserting the exact fallback string from Finding 2, since no test
previously exercised the `not src_w or not src_h` guard at all.

### Numbers verified

Computed directly against the fixed code (not asserted from memory):

```
$ .venv-local/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'helpers'); import render
print(render.cover_crop_filter(1920, 1080, center_x=0.5))
print(render.cover_crop_filter(720, 1600, center_x=0.5))
print(render.cover_crop_filter(0, 0, center_x=0.5))
print(render.cover_crop_filter(0, 0, center_x=0.5, draft=True))
"
scale=3414:1920,crop=1080:1920:x=1167:y=0
scale=1080:2400,crop=1080:1920:x=0:y=240
scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920
scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280
```

- **1920x1080 source → 1080x1920 canvas, center_x=0.5**: `scale=3414:1920`,
  `x=1167`, `y=0`. `scale_f = max(1080/1920, 1920/1080) = 1.77778`;
  `scaled_w = round(1920*1.77778) = 3413` → even-adjusted `3414`;
  `scaled_h = round(1080*1.77778) = 1920` (already even). Legacy centred
  `x = (3414-1080)//2 = 1167`; `y = (1920-1920)//2 = 0`. This matches the
  reviewer's expected `scaled_w=3414`/`scaled_h=1920` — no discrepancy found.
- **720x1600 source (taller than 9:16) → 1080x1920 canvas, center_x=0.5**:
  `scale=1080:2400`, `x=0`, `y=240`. `scale_f = max(1080/720, 1920/1600)
  = 1.5`; `scaled_w = round(720*1.5) = 1080` (even); `scaled_h =
  round(1600*1.5) = 2400` (even). `y = (2400-1920)//2 = 240` — matches the
  reviewer's worked example (`// 2` gives 240, the old `// 4` gave 120).

### Commands run

```
.venv-local/Scripts/python.exe -m pytest tests/test_render_cover.py -v
.venv-local/Scripts/python.exe -m pytest tests/ -v
```

### Actual test output

`tests/test_render_cover.py` (9 tests, all new/updated in this pass):
```
tests/test_render_cover.py::test_scale_dimensions_match_known_case PASSED [ 11%]
tests/test_render_cover.py::test_centered_crop_matches_legacy_behaviour PASSED [ 22%]
tests/test_render_cover.py::test_y_offset_is_centred_not_biased_for_tall_source PASSED [ 33%]
tests/test_render_cover.py::test_off_centre_subject_shifts_crop_left PASSED [ 44%]
tests/test_render_cover.py::test_crop_never_leaves_the_frame PASSED      [ 55%]
tests/test_render_cover.py::test_draft_uses_smaller_canvas PASSED        [ 66%]
tests/test_render_cover.py::test_already_vertical_source_is_not_cropped_horizontally PASSED [ 77%]
tests/test_render_cover.py::test_missing_dimensions_fall_back_to_legacy_safe_filter PASSED [ 88%]
tests/test_render_cover.py::test_missing_dimensions_fall_back_to_legacy_safe_filter_draft PASSED [100%]

9 passed in 0.05s
```

Repo-root `tests/` suite:
```
FAILED tests/test_api.py::test_transcribe_requires_explicit_click - assert 400 == 202
FAILED tests/test_api.py::test_transcribe_409_when_busy - assert 400 == 409
FAILED tests/test_claude.py::test_first_turn_has_no_resume - AssertionError: assert 'C:\\Users\\...\\claude.exe' == 'claude'
FAILED tests/test_cut_picks.py::test_apply_cut_picks_zooms_and_drops - assert 1.28 == 1.35
FAILED tests/test_talking_head.py::test_apply_bin_inserts_broll - assert False
FAILED tests/test_visual_picks.py::test_apply_visuals_inserts_broll_and_graphic - assert False

6 failed, 86 passed, 1 warning in 14.93s
```

Failure list is exactly the 6 pre-existing failures named in the fix brief's
verified baseline — no new failures, no failures fixed incidentally. Pass
count rose from the branch's prior 82 to 86 (the 4 new tests added in this
pass: `test_scale_dimensions_match_known_case`,
`test_y_offset_is_centred_not_biased_for_tall_source`,
`test_missing_dimensions_fall_back_to_legacy_safe_filter`,
`test_missing_dimensions_fall_back_to_legacy_safe_filter_draft`).

### Files changed (this pass)

- `D:\Desktop\Desktop Files\Projects\clipcut\helpers\render.py` —
  `cover_crop_filter`'s `y` divisor `// 4` → `// 2` (comment removed); its
  `not src_w or not src_h` guard now returns the true legacy filter string
  (no `x=0:y=0`); `extract_segment`'s `cover` branch now passes `(0, 0)`
  through on probe failure instead of a guessed `(1920, 1080)`.
- `D:\Desktop\Desktop Files\Projects\clipcut\tests\test_render_cover.py` —
  added regex-based `_parse_scale`/`_parse_crop_xy` oracle helpers;
  `test_crop_never_leaves_the_frame` now asserts against parsed values
  instead of a recomputed formula; `test_centered_crop_matches_legacy_behaviour`
  now asserts concrete `x`/`y`; added `test_scale_dimensions_match_known_case`,
  `test_y_offset_is_centred_not_biased_for_tall_source`,
  `test_missing_dimensions_fall_back_to_legacy_safe_filter`, and its
  `draft=True` variant.

### Commit

`a23f091` — "fix: restore cover-crop backwards compat and probe-failure
safety" (`helpers/render.py`, `tests/test_render_cover.py` only; explicit
paths staged, `git status --short` confirmed before committing).
