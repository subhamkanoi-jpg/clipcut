# Task 12 Report: Parity test, then delete the old renderer

## Summary

Gate passed, `backend/render_engine.py` deleted, all consumers repointed to
the `plan.render_plan` / `plan.assemble` / `helpers/` pipeline. Both test
suites are green against their baselines. One commit created.

## Starting state (this is a retry)

`backend/tests/test_render_parity.py` and `backend/tests/fixtures/parity_src.mp4`
already existed on disk (untracked) from a previous attempt, but with the
**old, buggy** assertions (`old_meta["duration"]`, `old_meta["width"]` etc. —
comparing the renderers' self-reported return dicts). `backend/render_engine.py`
was still present; `backend/handlers/export.py` and `backend/server.py` were
untouched (still on the old renderer). I verified this with a diff against the
corrected brief before doing anything else, then overwrote the test file with
the brief's verbatim corrected version.

## Step 1: Fixture

Already present and valid: `backend/tests/fixtures/parity_src.mp4`, ffprobed as
1920x1080, 6.000000s. `backend/tests/fixtures/README.md` already matched the
brief. No regeneration needed.

## Step 2: Parity test (verbatim from brief, then one additional fix)

Wrote `backend/tests/test_render_parity.py` to match the brief's corrected
code exactly (probing both output files via `render_plan._probe_out()`
instead of trusting either renderer's return dict).

**Additional defect found and fixed (not in the brief, discovered while
working the plan forward to Step 5/6):** `test_both_renderers_agree` does
`import render_engine` inside the test body. The brief's Step 5 deletes
`backend/render_engine.py` but never revisits this test. Once the file is
gone, every subsequent run of the suite would hit `ModuleNotFoundError` on
that import — not a skip, a hard collection-time-adjacent failure — which
directly contradicts Step 6 ("Expected: PASS in both") and the requirement
that "backend must be all-green" after this task. This is a real structural
gap, distinct from the already-fixed duration-key defect.

Fix: replaced the plain `import render_engine` with
`pytest.importorskip("render_engine", reason=...)`. This does **not** touch
any assertion in the gate — the exact same comparisons ran and passed before
I made this change (see "Gate run" below) — it only changes what happens on
runs *after* the gate has already licensed the deletion. Post-deletion, the
test skips cleanly with a clear reason instead of erroring; `test_original_aspect_keeps_source_geometry`
(which never imports `render_engine`) continues to run and pass forever as a
permanent regression test for `render_plan.render`.

## Step 3: Gate run (render_engine.py still present)

```
cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_render_parity.py -v
tests\test_render_parity.py::test_both_renderers_agree PASSED
tests\test_render_parity.py::test_original_aspect_keeps_source_geometry PASSED
2 passed in 16.46s
```

Re-ran after adding `importorskip` (render_engine.py still present at that
point) to confirm the fix didn't change gate behavior — same result, 2 passed.

### Actual parity numbers (both renderers, same input)

Captured via a throwaway instrumentation script mirroring the test exactly
(not committed).

**Test 1 — 9:16, karaoke on, burn on, cinematic off:**

| | old renderer (`render_engine.render_export`) | new renderer (`render_plan.render`) |
|---|---|---|
| self-reported dict | `{'width': 1080, 'height': 1920, 'aspect': '9:16', 'moves': [], 'punches': [], 'punch_count': 0, 'center_x': 0.5, 'caption_events': 3, 'karaoke': True}` | `{'width': 1080, 'height': 1920, 'duration': 3.749349}` |
| ffprobed output file (`_probe_out`) | `{'width': 1080, 'height': 1920, 'duration': 3.8}` | `{'width': 1080, 'height': 1920, 'duration': 3.749349}` |

- Geometry: 1080x1920 == 1080x1920 (exact match)
- Duration delta: `abs(3.749349 - 3.8) = 0.0507` — well within the 0.25s tolerance
- Old renderer's self-reported width/height (1080x1920) sanity-checked against its own probe: match

**Test 2 — aspect "original":**

`render_plan.render` output: `{'width': 1920, 'height': 1080, 'duration': 3.728353}` — width > height, source geometry preserved, as asserted.

**Conclusion: no real divergence. Both renderers agree on geometry exactly and
on duration within encoder/fade slop. Gate passed for a real reason, not a
test artifact.**

## Step 4: Switch the export handler

`backend/handlers/export.py`:
- Imports: dropped `import render_engine`, added `from plan import assemble, render_plan`.
- `cb(p)` → `cb(p, stage)`, now just forwards `(p, stage)` to `ctx.progress` and
  the DB update (no more manual stage-from-progress-threshold inference — the
  new pipeline names its own stages).
- Render call replaced with:
  ```python
  state = compute_cut_state({**doc, "reel_settings": reel})
  edl = assemble.from_project({**doc, "caption_style": style_key}, state)
  meta = render_plan.render(
      edl, pdir, out_path,
      words=doc.get("words") or [],
      progress_cb=cb,
      cancel_cb=ctx.cancelled,
  )
  ```
  Matches the brief verbatim.

Note (not part of my file list, flagging only): the trailing
`shutil.rmtree(pdir / "work", ignore_errors=True)` at the end of `run()` was
written for the old renderer's `work_dir=pdir/"work"`. The new pipeline's
`render_plan.render` uses `materialize.edit_dir(project_dir)` → `pdir/"edit"`
(with a `work` subdir under that) instead. That rmtree call is now a
harmless no-op (ignore_errors=True, target never exists) rather than doing
real cleanup — `pdir/edit/` (segments, base.mp4, composite.mp4, captions.ass)
is never cleaned up post-export. Not in Task 12's file list and doesn't
affect correctness or any test, so left untouched; flagging as a disk-hygiene
follow-up.

## Step 5: Delete the old renderer, repoint everything

- `git rm backend/render_engine.py`
- `backend/server.py`: removed `import render_engine`; added `import sys`,
  `import probe as probe_mod`, and (matching the brief's exact snippet)
  ```python
  sys.path.insert(0, str(ROOT.parent / "helpers"))
  import captions_ass
  ```
  (reused the file's existing `ROOT = Path(__file__).parent`, defined above
  these imports, instead of re-deriving `_Path(__file__).parent.parent` —
  same value, avoids a second Path alias).
  - `complete_upload`: `render_engine.probe` → `probe_mod.probe`
  - `complete_upload`: `render_engine.make_thumbnail` → `probe_mod.make_thumbnail`
  - `get_thumbnail`: `render_engine.make_thumbnail` → `probe_mod.make_thumbnail`
  - `set_style`: `render_engine.CAPTION_STYLES` → `captions_ass.CAPTION_STYLES`
  - `start_export`: `render_engine.CAPTION_STYLES` → `captions_ass.CAPTION_STYLES`
  - `list_styles`: `render_engine.CAPTION_STYLES.keys()` → `captions_ass.CAPTION_STYLES.keys()`
- New `backend/probe.py`: `probe()`, `make_thumbnail()`, `_even()`, `_run()`
  copied verbatim from the deleted file, plus the `HDR_TRANSFERS` constant
  `probe()` depends on (not one of the four named functions, but required for
  `probe()` to actually work — omitting it would have been a silent break).

### Consumer not in the brief's list, found by grep, and fixed

`backend/tests/test_handler_export.py` monkeypatched
`eh.render_engine.render_export` in all three of its tests. Once `export.py`
stopped importing `render_engine`, `eh.render_engine` would `AttributeError`.
Updated all three tests to monkeypatch `eh.render_plan.render` instead, and
changed the mock lambdas from `lambda **kw: ...` to `lambda *a, **kw: ...`
since `render_plan.render` is called with positional args
(`edl, pdir, out_path`) where the old call was fully keyword. Return shapes
were already `{"width", "height", "duration"}` in the mocks, so no change
needed there — they already matched the new contract.

### Grep proof nothing else dangles

`grep -rn render_engine` across the whole repo, `*.py` only, after the delete
and all repointing:

```
backend\tests\test_render_parity.py:1:"""Gate for deleting render_engine.py: both renderers must agree."""
backend\tests\test_render_parity.py:29:    Once Task 12 Step 5 runs `git rm backend/render_engine.py`, this import
backend\tests\test_render_parity.py:34:    render_engine = pytest.importorskip(
backend\tests\test_render_parity.py:35:        "render_engine",
backend\tests\test_render_parity.py:36:        reason="render_engine.py was deleted after this gate licensed its removal (Task 12)",
backend\tests\test_render_parity.py:51:    old_meta = render_engine.render_export(
tests\test_captions_ass.py:16:# backend/render_engine.py's `for r_start, r_end in ranges` /
backend\plan\render_plan.py:128:        # path onward when there is something to burn, matching render_engine.py's
```

Every remaining hit is either (a) inside the gate test itself, guarded by
`importorskip` so it never executes a real import after deletion, or (b) a
comment in another file (`test_captions_ass.py`, `render_plan.py`) that
references the old file historically — no live imports. Docs
(`docs/superpowers/plans/...`, `docs/superpowers/specs/...`, `memory/PRD.md`)
also mention `render_engine.py` but are historical planning artifacts, not
code, and out of this task's file list.

Also checked: `backend/reframe.py` (the OpenCV face-centering module,
`subject_center()`) was render_engine's only consumer. It is now imported
nowhere in the repo. `plan/assemble.py` reads `doc.get("subject_center_x", 0.5)`
but nothing in the current codebase (checked across all of `backend/*.py`)
ever sets that field — so every 9:16 export via the new pipeline center-crops
at 0.5 (dead center) rather than face-detecting. This predates Task 12:
`assemble.py` is a committed interface from Tasks 5-11, not in my file list,
and not something this task's brief asked me to touch. Flagging as a
functional-regression concern for a separate task, not fixing here.

Smoke-tested the actual runtime wiring (not just grep):
- `import server` loads cleanly; `server.list_styles()` →
  `{'styles': ['bold', 'neon', 'boxed', 'minimal']}` — same 4 keys as
  `render_engine.CAPTION_STYLES` had.
- `import handlers.export as eh` loads cleanly; `eh.render_plan` and
  `eh.assemble` are the real modules; `hasattr(eh, "render_engine")` is `False`.
- `import probe; probe.probe(fixture)` → `{'width': 1920, 'height': 1080, 'duration': 6.0, 'hdr': False}`;
  `probe.make_thumbnail(...)` produced a real 7815-byte JPEG.

Removed a stale `backend/__pycache__/render_engine.cpython-312.pyc` (not
git-tracked; `__pycache__/` is gitignored — just tidiness).

## Step 6: Full suite runs

**Backend** (`cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/ -v --ignore=tests/test_reel_backend.py --ignore=tests/test_backend_e2e.py`):

```
55 passed, 1 skipped in 5.47s
```

The 1 skip is `test_render_parity.py::test_both_renderers_agree`, skipped by
design post-deletion (see Step 2). `test_original_aspect_keeps_source_geometry`
passes. Baseline was 54 passed / 0 failed; now 55 passed / 1 skipped / 0
failed — the extra pass is the parity file's second test, which didn't exist
in a passing state at baseline-capture time. **0 failures, all green.**

Verified `opencv-python-headless` is still pinned at `4.11.0` before and after
the run (`cv2.__version__` → `4.11.0`).

**Root** (`.venv-local/Scripts/python.exe -m pytest tests/ -q` from repo root):

```
6 failed, 102 passed, 1 warning in 14.04s

FAILED tests/test_api.py::test_transcribe_requires_explicit_click
FAILED tests/test_api.py::test_transcribe_409_when_busy
FAILED tests/test_claude.py::test_first_turn_has_no_resume
FAILED tests/test_cut_picks.py::test_apply_cut_picks_zooms_and_drops
FAILED tests/test_talking_head.py::test_apply_bin_inserts_broll
FAILED tests/test_visual_picks.py::test_apply_visuals_inserts_broll_and_graphic
```

Exact match to the stated baseline (102 passed / 6 failed, same six names).
No new failures, no fewer.

Additionally verified the fixture-absent path: moved
`parity_src.mp4` out, ran `test_render_parity.py` — both tests skipped
cleanly (`2 skipped in 0.14s`, reason "fixture not generated"), then restored
the file and re-ran the full backend suite to confirm the 55/1/0 result held.

## Step 7: Skipped

Per controller instruction — the manual browser end-to-end (Step 7 in the
brief) is verified separately by the controller. Not performed here.

## Step 8: Commit

**Did not** run the brief's literal `git add -A` (explicitly flagged in my
instructions as wrong — it caused a prior incident where 2,891 files of local
venv got committed). Staged explicit paths only:

```
git add .gitignore backend/handlers/export.py backend/server.py \
        backend/tests/test_handler_export.py backend/probe.py \
        backend/tests/fixtures/README.md backend/tests/test_render_parity.py
```

(`backend/render_engine.py`'s deletion was already staged by the earlier
`git rm`.)

`git status --short` after staging showed exactly the 8 intended paths
(1 gitignore edit, 3 modified, 1 deleted, 3 new) plus a long pre-existing list
of unrelated modified `.superpowers/sdd/*.md` files that were already dirty
in the working tree *before I started this task* (leftover state from
whatever process populated this retry's environment — I diffed them and
confirmed they're unrelated to render_engine/parity, e.g. `task-12-brief.md`'s
working-tree diff shows it changing from an old "chat SSE" task's content to
this task's content, clearly pre-existing drift, not something I touched).
Left every one of those `.superpowers/sdd/*.md` files unstaged and
uncommitted, per the staging-discipline instruction.

Commit: **`05cd377`** — "refactor: single renderer, delete render_engine.py"

```
 8 files changed, 179 insertions(+), 423 deletions(-)
 create mode 100644 backend/probe.py
 delete mode 100644 backend/render_engine.py
 create mode 100644 backend/tests/fixtures/README.md
 create mode 100644 backend/tests/test_render_parity.py
```

Post-commit `git status --short` confirms only the pre-existing unrelated
`.superpowers/sdd/*.md` modifications remain, nothing else — no stray files,
no venv, no video blob.

`backend/tests/fixtures/parity_src.mp4` is gitignored
(`git check-ignore -v` confirms it matches the new `.gitignore` rule) and was
never staged.

## Files changed

- `D:\Desktop\Desktop Files\Projects\clipcut\.gitignore` (added fixture ignore rule)
- `D:\Desktop\Desktop Files\Projects\clipcut\backend\handlers\export.py` (switched to `plan.render_plan`/`plan.assemble`)
- `D:\Desktop\Desktop Files\Projects\clipcut\backend\server.py` (dropped `render_engine`, added `captions_ass` + `probe`)
- `D:\Desktop\Desktop Files\Projects\clipcut\backend\probe.py` (new — `probe`, `make_thumbnail`, `_even`, `_run`, `HDR_TRANSFERS`)
- `D:\Desktop\Desktop Files\Projects\clipcut\backend\tests\test_handler_export.py` (mocks repointed to `render_plan.render`)
- `D:\Desktop\Desktop Files\Projects\clipcut\backend\tests\test_render_parity.py` (new — corrected gate test + `importorskip` fix)
- `D:\Desktop\Desktop Files\Projects\clipcut\backend\tests\fixtures\README.md` (new)
- `D:\Desktop\Desktop Files\Projects\clipcut\backend\render_engine.py` (deleted)
- `D:\Desktop\Desktop Files\Projects\clipcut\backend\tests\fixtures\parity_src.mp4` (untracked, gitignored, present on disk for local runs)

## Self-review

- Gate ran and passed on real geometry/duration comparisons before any
  deletion happened — verified with an instrumentation script that the
  numbers are real (1080x1920 both sides, 3.8s vs 3.749s), not fabricated
  or assumed.
- The one deviation from the brief's literal text (the `importorskip` fix)
  does not touch a single assertion inside the gate — it only changes
  behavior for runs *after* the gate already passed and licensed the
  deletion, converting a permanent post-deletion crash into a documented
  skip. I did not weaken, loosen, or remove any comparison the brief specified.
  I'm flagging this prominently rather than quietly folding it in, per the
  spirit of "if something's wrong, stop and report" that governed the
  previous attempt's correct behavior.
  I considered instead deleting `test_both_renderers_agree` entirely (its
  job is done once licensing the deletion) but kept it with `importorskip`
  so the historical proof — exact assertions, exact tolerances — stays in
  the repo and self-documents why it's skipping, rather than disappearing.
- Verified server.py and handlers/export.py actually import and run, not
  just pass grep — direct module load + smoke calls, not inference.
  `probe.py` was executed end-to-end against the real fixture (probe +
  thumbnail generation), not just visually diffed against the deleted file.
- Verified the fixture-absent path actually skips cleanly (moved the file
  out, ran the suite, saw 2 skipped, restored it, reran to confirm the full
  count).
- Did not touch the pre-existing unrelated dirty `.superpowers/sdd/*.md`
  files in the working tree, despite `git status` prompting them repeatedly —
  confirmed via diff that they predate this session and are out of scope.
- Did not fix the `subject_center_x` / `reframe.py` dead-code gap or the
  stale `pdir/"work"` rmtree — both are real but out of this task's committed
  file list, and this is explicitly the one destructive task in the plan
  where scope discipline matters most. Flagged both instead of silently
  fixing or silently ignoring.

## Concerns for the controller

1. **`backend/reframe.py`'s `subject_center()` (OpenCV face-centering) is
   dead code post-migration.** Nothing calls it anymore; `assemble.py` reads
   `doc.get("subject_center_x", 0.5)` but nothing sets that field, so every
   new 9:16 export centers at 0.5 instead of face-detecting the speaker. This
   is a functional regression versus the behavior described in the repo's own
   commit history ("center_x 0.27 on an off-centre landscape clip"). Predates
   Task 12 (assemble.py is a Tasks 5-11 committed interface), not in my file
   list — needs its own task.
2. **Stale work-dir cleanup.** `handlers/export.py`'s
   `shutil.rmtree(pdir / "work", ignore_errors=True)` targets the old
   renderer's work directory, which the new pipeline never creates (it uses
   `pdir/edit/work` via `materialize.edit_dir`). Harmless no-op today, but
   `pdir/edit/` (segments, base.mp4, composite.mp4, captions.ass) is now
   never cleaned up after a successful export. Not in my file list; flagging
   for a follow-up.
3. The working tree had ~20 unrelated modified `.superpowers/sdd/*.md` files
   present before I started (pre-existing drift, not from this session).
   Left untouched per staging discipline; the controller may want to
   reconcile or discard them separately.
