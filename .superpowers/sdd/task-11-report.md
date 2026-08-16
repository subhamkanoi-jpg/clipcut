# Task 11 Report: Render an EDL v2 through the helpers pipeline

## Status: DONE_WITH_CONCERNS

(One deliberate, documented deviation from the brief's reference snippet —
see "Deviation from brief" below. Everything else matches the brief verbatim.)

## What was implemented

- `backend/plan/render_plan.py` — `render(plan, project_dir, out_path, words,
  progress_cb=None, cancel_cb=None) -> dict`, plus stage helpers `_extract_all`,
  `_concat`, `_composite`, `_master`, `_probe_out`.
- `backend/tests/test_render_plan.py` — 3 tests, created verbatim from the brief.

Pipeline implemented, in fixed order:
1. Validate the plan (`plan.model.validate`); raise `ValueError` listing all
   errors, before any work, if invalid.
2. Materialize `edit/edl.json` via `plan.materialize.write` / `edit_dir`.
3. **Cut** — `_extract_all`: per-range `helpers.render.extract_segment`, with
   `cover=True` + `reframe.center_x` when `reframe.aspect == "9:16"`, and a
   passthrough (no cover crop) for `"original"`.
4. **Concat** — `_concat`: `helpers.render.concat_segments` (lossless `-c copy`).
5. **Caption prep** — if `captions.burn` and `words` is non-empty, probe the
   concatenated base's real width/height (`_probe_out(base)`) and build the
   `.ass` file via `helpers.captions_ass.build_ass`, converting EDL v2's
   dict-shaped `ranges` into `(start, end)` tuples (`captions_ass.timeline_chunks`
   unpacks `for r_start, r_end in ranges`, so dicts would break it).
6. **Composite** — `_composite`: `helpers.render.build_final_composite(base,
   overlays, subs_path, out, edit_dir)`. `overlays` is filtered to
   `enabled=True` items; since Task 10's assembler never populates `overlays`,
   this list is always empty today, and `build_final_composite` handles that
   cleanly on its own (copies `base` straight to `out` when both overlays and
   subtitles are absent; burns subtitles-only via a single-clause filter graph
   when only subtitles are present — no dead overlay code was added on our
   side to special-case the empty list).
7. **Master** — `_master`: `helpers.render.apply_loudnorm_two_pass(src, out_path)`
   (full two-pass; the `preview` flag is left at its default `False`).
8. Return `_probe_out(out_path)` → `{"width", "height", "duration"}`.

Progress stages reported via `progress_cb(percent, stage)`: `"cutting"` →
`"compositing"` → `"captioning"` → `"mastering"` → `"done"`, matching the
test's expected order exactly.

Cancellation: `cancel_cb()` is checked (raising `worker.Cancelled` when it
returns `True`) before: extraction, concat, caption-building (when captions
will actually be built), compositing, and mastering.

## TDD evidence

**RED** — `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_render_plan.py -v`

```
ERROR collecting backend/tests/test_render_plan.py
ImportError: cannot import name 'render_plan' from 'plan' (...backend\plan\__init__.py)
=========================== short test summary info ===========================
ERROR tests\test_render_plan.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
============================== 1 error in 0.32s ===============================
```
Matches the brief's expected failure exactly.

**GREEN** — same command, after implementation (and after the cancellation-checkpoint fix, see below):

```
tests\test_render_plan.py::test_invalid_plan_raises_with_reasons PASSED  [ 33%]
tests\test_render_plan.py::test_progress_callback_reports_named_stages PASSED [ 66%]
tests\test_render_plan.py::test_cancel_before_extract_raises PASSED      [100%]

============================== 3 passed in 0.19s ==============================
```

## Backend suite regression result

`cd backend && ../.venv-local/Scripts/python.exe -m pytest --ignore=tests/test_reel_backend.py --ignore=tests/test_backend_e2e.py`

```
tests\test_assemble.py ........                                          [ 15%]
tests\test_handler_export.py ...                                         [ 21%]
tests\test_handler_transcribe.py ..                                      [ 25%]
tests\test_jobs.py ............                                          [ 49%]
tests\test_materialize.py .....                                          [ 58%]
tests\test_plan_model.py ..........                                      [ 78%]
tests\test_render_plan.py ...                                            [ 84%]
tests\test_worker.py ........                                            [100%]

============================== 51 passed in 2.28s ==============================
```

All 51 tests pass — zero regressions. No test in this run touches ffmpeg/ffprobe
(the heavy stages in `test_render_plan.py` are monkeypatched, per the brief).

## Files changed

- `D:\Desktop\Desktop Files\Projects\clipcut\backend\plan\render_plan.py` (new)
- `D:\Desktop\Desktop Files\Projects\clipcut\backend\tests\test_render_plan.py` (new)

`backend/render_engine.py` was not touched (Task 12's parity test needs it alive).

## Signature verification (before writing calls)

Read `helpers/render.py` and `helpers/captions_ass.py` in full before writing
any call sites. Every signature the brief's snippet uses matches the real code
exactly — no mismatches found, nothing to resolve:

- `extract_segment(source, seg_start, duration, grade_filter, out_path,
  preview=False, draft=False, zoom=1.0, cover=False, center_x=0.5)` — matches.
- `concat_segments(segment_paths, out_path, edit_dir)` — matches.
- `build_final_composite(base_path, overlays, subtitles_path, out_path,
  edit_dir, force_style_str=SUB_FORCE_STYLE)` — matches.
- `apply_loudnorm_two_pass(input_path, output_path, preview=False)` — matches
  (brief calls it with 2 positional args, defaulting `preview`).
- `resolve_grade_filter(grade_field)` — matches.
- `captions_ass.build_ass(words, ranges, out_path, style, width, height,
  karaoke=True, fonts_dir=None)` — matches, including the `ranges` contract:
  `timeline_chunks` does `for r_start, r_end in ranges`, confirmed by reading
  `helpers/captions_ass.py` line 71 — the brief's tuple-conversion
  (`[(r["start"], r["end"]) for r in plan["ranges"]]`) is correct and was kept
  verbatim, not "simplified" back to dicts.
- `worker.Cancelled` — confirmed to live only in `backend/worker.py` (no
  `helpers/worker.py` exists), so `import worker` with `backend/` first on
  `sys.path` resolves unambiguously.
- Confirmed no module name collisions between `backend/` and `helpers/` for
  `render`, `worker`, or `captions_ass` (checked `backend/render_engine.py` is
  named differently from `helpers/render.py`, and `backend/worker.py` /
  `helpers/captions_ass.py` have no same-named counterpart in the other
  directory).

## Deviation from the brief's reference snippet

**Missing cancellation checkpoint before the composite stage.** The brief's
Step-3 code (and the task's own "Before You Begin" framing) is offered as the
reference implementation, but the Global Constraints section states plainly:
"Cancellation must be checked before each expensive stage," and the task's
own interface contract for `render()` says it "Calls `cancel_cb()` before each
ffmpeg spawn." Tracing the brief's snippet, `check_cancel()` was called before
`_extract_all`, before `_concat`, conditionally before caption-building, and
before `_master` — but **not** before `_composite`, even though
`_composite` → `helpers.render.build_final_composite` always spawns an ffmpeg
process (either a straight `-c copy` passthrough or a full `filter_complex`
re-encode). This is a real gap in the brief's snippet, not a signature
mismatch, so it wasn't something the "stop and ask" instruction (which is
scoped to signature mismatches) applies to — I fixed it inline as a one-line,
test-safe addition:

```python
    check_cancel()
    composited = _composite(base, plan, subs_path, work_dir, edit_dir)
```

Verified this doesn't change progress-tick semantics (no new `tick()` call
added) and doesn't break any of the 3 brief tests or the other 48 backend
tests — reran both suites after the change, all still green (see above).
Flagging this as `DONE_WITH_CONCERNS` only because it is a deliberate,
reasoned departure from the brief's literal code, even though it strictly
satisfies the brief's own stated cancellation contract.

## Self-review findings

1. **Stage order** — cut → concat → composite (overlays + captions burned in
   one ffmpeg filter graph, subtitles clause applied last within
   `build_final_composite`) → two-pass loudnorm. Confirmed correct against the
   task's fixed stage order requirement.
2. **Cancellation checkpoints** — fixed the missing one before `_composite`
   (see Deviation above). All four ffmpeg-spawning stages (extract, concat,
   composite, master) now have a `check_cancel()` immediately before them.
   Note: `_extract_all` still checks cancellation once before the whole batch,
   not once per per-range `ffmpeg` spawn inside its loop — a literal reading
   of "before each ffmpeg spawn" would check per-segment too. I left this as
   in the brief (matches the one test that exercises cancellation, which
   expects the whole extract stage to be skipped on a pre-set cancel flag) —
   threading `cancel_cb` into the per-segment loop would be a larger, untested
   change beyond this task's scope. Flagging as a possible follow-up, not
   fixing now.
3. **Progress-tick label vs. real work mismatch** — the `"compositing"` tick
   fires immediately before `_concat` (a cheap `-c copy` stream copy), not
   before `_composite` (where the actual overlay/caption compositing ffmpeg
   work happens — currently untouched by any tick since no new tick was added
   alongside the new cancellation check). This is exactly what the brief
   specifies and what the progress-order test checks, so it was kept as-is;
   noting it here as a minor semantic quirk rather than a bug, since the test
   only asserts stage-name *order*, not that tick position matches the
   heaviest work in that stage.
4. **Captions get real output dimensions** — confirmed: `_probe_out(base)` is
   called on the post-concat base video, which is already scaled/cropped to
   final output dimensions by `extract_segment` (cover-crop to 1080x1920 for
   `9:16`, native scale for `original`). Concat is lossless (`-c copy`) so it
   doesn't change dimensions, and `build_final_composite` doesn't rescale the
   base track either (only overlay graphics get scaled to match it, and
   overlays are always empty today). `apply_loudnorm_two_pass` also uses
   `-c:v copy`, so final output dimensions equal what was probed for caption
   sizing. No dimension mismatch risk.
5. **Empty-overlays path** — confirmed clean. `_composite` filters
   `plan.get("overlays")` to `enabled=True` items (empty list today, per Task
   10 not populating it), and `helpers.render.build_final_composite` already
   handles `overlays=[]` correctly on its own: full passthrough copy when
   subtitles are also absent, single-clause subtitle-only filter graph when
   subtitles are present. No overlay-specific code was added on our side —
   nothing to build for a feature nothing produces yet, as instructed.
6. **Invalid-plan-raises-before-any-work** — confirmed: `model.validate(plan)`
   is the very first line of `render()`, before `materialize.write`,
   `edit_dir.mkdir`, or any stage runs.

## Concerns

- The one documented deviation above (added cancellation check before
  `_composite`) is a considered, tested, and reported change — flagging as
  `DONE_WITH_CONCERNS` per the reporting convention for any deliberate
  departure from the brief's literal snippet, even though it makes the
  implementation strictly more correct against the task's own stated
  constraints.
- Not independently exercised against real ffmpeg/ffprobe (out of scope per
  the brief: "keep it that way; this task's tests must stay fast" — the heavy
  stages are monkeypatched). Task 12's dual-render parity test is the real
  end-to-end verification of behavioral equivalence with
  `backend/render_engine.py`, as described in the plan.

## Fix pass

Reviewer found two Important defects and a coverage gap in the original
implementation above. All three fixed in this pass, in
`backend/plan/render_plan.py` and `backend/tests/test_render_plan.py` only.
`backend/render_engine.py`, `helpers/render.py`, and `helpers/captions_ass.py`
were read but not modified, per the constraints.

### Finding 1 — empty subtitle file could reach ffmpeg

`captions_ass.build_ass(...)`'s return value (an event count) was being
discarded, so a 0-byte, headerless `.ass` file (written whenever
`timeline_chunks` produces no chunks — i.e. none of the plan's kept ranges
overlap any transcribed word) would still get handed to `_composite` as
`subs_path`. `helpers/render.py`'s `build_final_composite` only checks
`subtitles_path is not None and subtitles_path.exists()` (`has_subs`,
line 646) — it never inspects size or content — so it would build a
`subtitles='<empty file>'` filter clause and pass that to ffmpeg.

Matched this to the renderer being replaced, `backend/render_engine.py`
lines 360–367:

```python
caption_count = 0
if burn:
    subs = work_dir / "captions.ass"
    caption_count = build_ass(words, ranges, subs, style, target[0], target[1], karaoke)
    if caption_count:
        captioned = work_dir / "captioned.mp4"
        burn_captions(base, subs, captioned)
        base = captioned
```

`render_engine.py` only proceeds to burn (`burn_captions`) when
`caption_count` is truthy; when `build_ass` returns 0, `base` is left
pointing at the pre-caption video and no subtitle path is ever used
downstream. That is the semantics `render_plan.py` needed to reproduce
without an actual burn step of its own (composite does the burning in this
pipeline) — i.e. never forward a subtitles path when the event count is 0.

**Fix applied** in `render()`'s caption-prep block: `build_ass` now writes to
a local `candidate_subs_path` and its return value is captured as
`caption_count`. `subs_path` (which starts as `None` and is what
`_composite` receives) is only assigned `candidate_subs_path` when
`caption_count` is truthy:

```python
caption_count = captions_ass.build_ass(
    words,
    [(r["start"], r["end"]) for r in plan["ranges"]],
    candidate_subs_path, style, probe["width"], probe["height"],
    karaoke=bool(caps.get("karaoke", True)),
    fonts_dir=captions_ass.FONTS_DIR,
)
if caption_count:
    subs_path = candidate_subs_path
```

When `caption_count == 0`, `subs_path` stays `None`, so `_composite` calls
`build_final_composite(base, overlays, None, out, edit_dir)` and `has_subs`
evaluates `False` — no subtitles clause, matching `render_engine.py`'s
behavior of never invoking `burn_captions` for a zero-event `.ass`.

### Finding 2 — cancellation not checked per ffmpeg spawn in extract

`_extract_all` looped over `plan["ranges"]` calling
`helpers_render.extract_segment` once per range (one ffmpeg process each)
with no cancellation check inside the loop; `render()` only called
`check_cancel()` once, immediately before invoking `_extract_all` as a
whole. Extract is the longest phase on a multi-segment plan, so a cancel
request could go unheard for the entire extract phase.

**Fix applied**: `_extract_all` now takes an optional `check_cancel`
callable and invokes it at the top of each loop iteration, before the
`extract_segment` call for that range:

```python
def _extract_all(plan, work_dir, cover, center_x, check_cancel=None):
    sources = plan["sources"]
    paths = []
    for i, r in enumerate(plan["ranges"]):
        if check_cancel:
            check_cancel()
        seg = work_dir / f"seg_{i:04d}.mp4"
        helpers_render.extract_segment(...)
        paths.append(seg)
    return paths
```

`render()`'s call site now passes its `check_cancel` closure through:
`segments = _extract_all(plan, work_dir, cover, center_x, check_cancel)`.
`check_cancel` still raises `worker.Cancelled` exactly as before (the
closure itself is unchanged); `_extract_all`'s role — building segment
paths — is otherwise untouched. The pre-loop `check_cancel()` call in
`render()` right before `tick(10, "cutting")` was left in place (cheap,
catches a cancel that arrived during/after concat-dir setup); the per-range
checks are what close the actual gap.

### Trivial cleanup — unused `project_dir` parameter

`_extract_all` took a `project_dir` argument it never read (it only uses
`work_dir`, derived from `project_dir` by the caller). Removed the
parameter from the signature and updated the one call site in `render()`
accordingly — signature is now
`_extract_all(plan, work_dir, cover, center_x, check_cancel=None)`.

### Finding 3 — caption path had zero test coverage

Added two new tests to `backend/tests/test_render_plan.py`, following the
existing monkeypatch style (stub `_extract_all`/`_concat`/`_composite`/
`_master`/`_probe_out` so no real ffmpeg/ffprobe runs):

- `test_captions_pass_tuple_ranges_to_build_ass` — monkeypatches
  `render_plan.captions_ass.build_ass` with a stub that captures its
  `ranges` argument and returns `3` (a non-zero count), and monkeypatches
  `_composite` to capture the `subs_path` it's called with. Asserts
  `captured["ranges"] == [(0.0, 2.0)]` and `all(type(r) is tuple for r in
  captured["ranges"])` — i.e. the EDL v2 dict-shaped ranges really do get
  converted to `(start, end)` tuples before reaching `build_ass`, not just
  in theory. Also asserts `composite_calls["subs_path"] is not None` so the
  non-zero-count path still forwards subtitles.
- `test_empty_captions_do_not_reach_composite` — stubs `build_ass` to
  reproduce its real zero-chunk behavior (`out_path.write_text(""); return
  0`) and asserts `composite_calls["subs_path"] is None` — this is the
  regression test for Finding 1; it fails against the pre-fix code (which
  passed `subs_path` unconditionally) and passes against the fix.

Also added `test_cancel_checked_before_each_extract_segment`, calling
`_extract_all` directly with a real 3-range plan and a `check_cancel` stub
that raises `worker.Cancelled` only once one segment has already been
extracted. Asserts exactly 1 `extract_segment` call happened (not 3) and
`check_cancel` was invoked twice (once permitted, once raising) — this is
the regression test for Finding 2; it fails against the pre-fix code
(which only checked cancellation once, before the whole loop, so all 3
segments would extract) and passes against the fix.

### Commands run

```
cd backend
../.venv-local/Scripts/python.exe -m pytest tests/test_render_plan.py -v
../.venv-local/Scripts/python.exe -m pytest --ignore=tests/test_reel_backend.py --ignore=tests/test_backend_e2e.py -v
```

### Actual output — `tests/test_render_plan.py -v`

```
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0 -- D:\Desktop\Desktop Files\Projects\clipcut\.venv-local\Scripts\python.exe
cachedir: .pytest_cache
rootdir: D:\Desktop\Desktop Files\Projects\clipcut
configfile: pyproject.toml
plugins: anyio-4.14.2
collecting ... collected 6 items

tests\test_render_plan.py::test_invalid_plan_raises_with_reasons PASSED  [ 16%]
tests\test_render_plan.py::test_progress_callback_reports_named_stages PASSED [ 33%]
tests\test_render_plan.py::test_cancel_before_extract_raises PASSED      [ 50%]
tests\test_render_plan.py::test_cancel_checked_before_each_extract_segment PASSED [ 66%]
tests\test_render_plan.py::test_captions_pass_tuple_ranges_to_build_ass PASSED [ 83%]
tests\test_render_plan.py::test_empty_captions_do_not_reach_composite PASSED [100%]

============================== 6 passed in 0.22s ==============================
```

### Actual output — full backend suite (excluding the two heavy suites)

```
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0 -- D:\Desktop\Desktop Files\Projects\clipcut\.venv-local\Scripts\python.exe
cachedir: .pytest_cache
rootdir: D:\Desktop\Desktop Files\Projects\clipcut
configfile: pyproject.toml
plugins: anyio-4.14.2
collecting ... collected 54 items

tests\test_assemble.py::test_ranges_mirror_keep_ranges PASSED            [  1%]
tests\test_assemble.py::test_captions_come_from_project_settings PASSED  [  3%]
tests\test_assemble.py::test_reframe_aspect_comes_from_reel_settings PASSED [  5%]
tests\test_assemble.py::test_original_aspect_is_preserved PASSED         [  7%]
tests\test_assemble.py::test_total_duration_is_the_sum_of_ranges PASSED  [  9%]
tests\test_assemble.py::test_output_validates PASSED                     [ 11%]
tests\test_assemble.py::test_center_x_defaults_to_half_when_absent PASSED [ 12%]
tests\test_assemble.py::test_center_x_is_carried_through_when_present PASSED [ 14%]
tests\test_handler_export.py::test_export_writes_done_state PASSED       [ 16%]
tests\test_handler_export.py::test_export_failure_records_error_not_stuck_processing PASSED [ 18%]
tests\test_handler_export.py::test_export_honours_cancellation_before_render PASSED [ 20%]
tests\test_handler_transcribe.py::test_transcribe_stores_words_and_marks_ready PASSED [ 22%]
tests\test_handler_transcribe.py::test_transcribe_failure_marks_project_error PASSED [ 24%]
tests\test_jobs.py::test_enqueue_creates_queued_job PASSED               [ 25%]
tests\test_jobs.py::test_claim_returns_job_and_marks_processing PASSED   [ 27%]
tests\test_jobs.py::test_claim_is_atomic_second_caller_gets_nothing PASSED [ 29%]
tests\test_jobs.py::test_claim_ignores_other_kinds PASSED                [ 31%]
tests\test_jobs.py::test_reconcile_requeues_expired_lease PASSED         [ 33%]
tests\test_jobs.py::test_reconcile_fails_job_past_max_attempts PASSED    [ 35%]
tests\test_jobs.py::test_cancel_flag_roundtrip PASSED                    [ 37%]
tests\test_jobs.py::test_finish_and_fail_set_terminal_status PASSED      [ 38%]
tests\test_jobs.py::test_heartbeat_extends_lease PASSED                  [ 40%]
tests\test_jobs.py::test_heartbeat_returns_false_when_not_processing PASSED [ 42%]
tests\test_jobs.py::test_set_progress_updates_fields PASSED              [ 44%]
tests\test_jobs.py::test_cancel_sets_terminal_status PASSED              [ 46%]
tests\test_materialize.py::test_write_creates_edl_json PASSED            [ 48%]
tests\test_materialize.py::test_write_is_idempotent PASSED               [ 50%]
tests\test_materialize.py::test_clean_removes_edit_dir_but_not_source PASSED [ 51%]
tests\test_materialize.py::test_clean_is_safe_when_nothing_exists PASSED [ 53%]
tests\test_materialize.py::test_clean_refuses_path_escaping_project_dir PASSED [ 55%]
tests\test_plan_model.py::test_new_plan_has_v2_shape PASSED              [ 57%]
tests\test_plan_model.py::test_overlay_factory_assigns_unique_ids PASSED [ 59%]
tests\test_plan_model.py::test_validate_accepts_minimal_valid_plan PASSED [ 61%]
tests\test_plan_model.py::test_validate_rejects_empty_ranges PASSED      [ 62%]
tests\test_plan_model.py::test_validate_rejects_unknown_source PASSED    [ 64%]
tests\test_plan_model.py::test_validate_rejects_inverted_range PASSED    [ 66%]
tests\test_plan_model.py::test_validate_rejects_negative_overlay_duration PASSED [ 68%]
tests\test_plan_model.py::test_validate_rejects_bad_aspect PASSED        [ 70%]
tests\test_plan_model.py::test_validate_rejects_center_x_out_of_range PASSED [ 72%]
tests\test_plan_model.py::test_validate_rejects_wrong_version PASSED     [ 74%]
tests\test_render_plan.py::test_invalid_plan_raises_with_reasons PASSED  [ 75%]
tests\test_render_plan.py::test_progress_callback_reports_named_stages PASSED [ 77%]
tests\test_render_plan.py::test_cancel_before_extract_raises PASSED      [ 79%]
tests\test_render_plan.py::test_cancel_checked_before_each_extract_segment PASSED [ 81%]
tests\test_render_plan.py::test_captions_pass_tuple_ranges_to_build_ass PASSED [ 83%]
tests\test_render_plan.py::test_empty_captions_do_not_reach_composite PASSED [ 85%]
tests\test_worker.py::test_run_once_returns_false_when_queue_empty PASSED [ 87%]
tests\test_worker.py::test_run_once_dispatches_and_finishes PASSED       [ 88%]
tests\test_worker.py::test_handler_exception_fails_the_job PASSED        [ 90%]
tests\test_worker.py::test_unknown_kind_fails_cleanly PASSED             [ 92%]
tests\test_worker.py::test_ctx_cancelled_reflects_flag PASSED            [ 94%]
tests\test_worker.py::test_handler_raising_cancelled_marks_job_cancelled PASSED [ 96%]
tests\test_worker.py::test_progress_warns_when_lease_lost PASSED         [ 98%]
tests\test_worker.py::test_running_as_script_registers_all_handlers PASSED [100%]

============================== 54 passed in 2.61s ===========================
```

54 passed (48 pre-existing + 6 in `test_render_plan.py`, up from 3), zero
failures, zero regressions. No test in this run touches real ffmpeg/ffprobe.

## Files changed (fix pass)

- `D:\Desktop\Desktop Files\Projects\clipcut\backend\plan\render_plan.py`
  (modified — Findings 1, 2, trivial cleanup)
- `D:\Desktop\Desktop Files\Projects\clipcut\backend\tests\test_render_plan.py`
  (modified — 3 new tests for Finding 3 / regression coverage of Findings 1 & 2)
- `backend/render_engine.py`, `helpers/render.py`, `helpers/captions_ass.py`
  — read only, not modified, per constraints.
