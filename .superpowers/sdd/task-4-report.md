# Task 4 Report: Move export onto the queue

## Status: DONE (Step 6 skipped per controller instruction)

## What I implemented

Followed the brief's Step 1-5, 7 verbatim (the brief's exact code was used
without modification — server.py matched the brief's assumed line ranges
exactly, so no design questions came up).

1. **`backend/handlers/export.py`** (new) — `run(ctx) -> dict` handler for
   job kind `"export"`. Reads `ctx.payload["caption_style"]` and
   `ctx.payload["reel"]`, lazily imports `compute_cut_state` from `server`
   (avoids a server->handler->server import cycle), calls
   `render_engine.render_export` with the same kwargs `_run_export` used,
   writes the same `export` sub-document shape on both success and failure,
   optionally uploads to Cloudinary, cleans up the `work/` dir, and
   self-registers via `worker.HANDLERS["export"] = run`. Checks
   `ctx.cancelled()` before starting the render and raises `Cancelled` if so.

2. **`backend/worker.py`** — `_register_handlers()` now also imports
   `handlers.export` (one line added).

3. **`backend/server.py`**:
   - Removed `import threading` and `import logging` (the latter became
     dead code once `_run_export`'s only `logging.exception` call was
     deleted).
   - `start_export` now updates the project doc (unchanged) then calls
     `jobs.enqueue(db, pid, "export", {"caption_style": ..., "reel": reel})`
     and returns `{"ok": True, "reel_settings": reel, "job_id": jid}` (the
     extra `job_id` key is additive; the brief specifies it and it doesn't
     change any field the frontend already reads).
   - Deleted `_run_export` entirely (the `threading.Thread(...).start()`
     call and the ~45-line function body).
   - Added `GET /api/jobs/{jid}` and `POST /api/jobs/{jid}/cancel`, placed
     next to `download_export`, before `list_styles`.

4. **`backend/tests/test_handler_export.py`** (new) — the brief's three
   tests verbatim: writes-done-state, failure-records-error, and
   honours-cancellation-before-render.

## TDD evidence

**RED** — `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_export.py -v`

```
ERROR collecting backend/tests/test_handler_export.py
ModuleNotFoundError: No module named 'handlers.export'
=========================== short test summary info ===========================
ERROR tests\test_handler_export.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
============================== 1 error in 0.37s ===============================
```

Matches the brief's predicted failure exactly.

**GREEN** — same command after implementation:

```
tests\test_handler_export.py::test_export_writes_done_state PASSED       [ 33%]
tests\test_handler_export.py::test_export_failure_records_error_not_stuck_processing PASSED [ 66%]
tests\test_handler_export.py::test_export_honours_cancellation_before_render PASSED [100%]
======================== 3 passed, 1 warning in 1.26s =========================
```

## Step 5 — grep check for threading

```
$ cd backend && grep -n "threading" server.py
(no output, exit code 1)
```

Confirmed: zero matches. `import threading` was removed.

## Full test suite

`cd backend && ../.venv-local/Scripts/python.exe -m pytest --ignore=tests/test_reel_backend.py --ignore=tests/test_backend_e2e.py -v`

```
24 passed, 1 warning in 1.77s
```

Covers: test_handler_export.py (3), test_handler_transcribe.py (2),
test_jobs.py (10), test_worker.py (7) — regressions checked, nothing broke.

Also sanity-checked `server.py` imports cleanly standalone and that the two
new routes are registered:

```
$ python -c "import server; print([r.path for r in server.app.routes if 'jobs' in r.path])"
server imports OK
['/api/jobs/{jid}', '/api/jobs/{jid}/cancel']
```

## Files changed

- `backend/handlers/export.py` (new)
- `backend/worker.py` (modified — one import line)
- `backend/server.py` (modified — removed threading/logging imports,
  replaced `start_export` body, deleted `_run_export`, added two job routes)
- `backend/tests/test_handler_export.py` (new)

## Commit

`395a0d1` — "feat: run export as a cancellable queued job"

Staged explicitly (`git add backend/handlers/export.py backend/worker.py
backend/server.py backend/tests/test_handler_export.py`), never `-A`/`.`/`-a`.
`git status --short` before committing confirmed only those four files were
staged. Note: `.superpowers/sdd/*.md` files (progress.md, task-1..4
brief/report) showed as modified in `git status` at commit time — I never
opened those with Write/Edit (only Read on task-4-brief.md), so this is
concurrent activity from another process/session, not something I caused. I
left them untouched and unstaged.

## Self-review

- **Completeness**: All of Steps 1-5 and 7 done exactly as specified. Step 6
  (manual end-to-end with worker + UI + real ElevenLabs key) skipped per
  controller instruction — see note below.
- **YAGNI**: Implementation is a literal transcription of the brief's given
  code with no embellishment. `handlers/export.py` mirrors the shape of
  `handlers/transcribe.py` (module-scope `worker.HANDLERS[...] = run`
  self-registration, `Ctx`-based progress/cancellation).
- **Tests verify real behavior against real Mongo**: Yes. The `db` fixture
  connects to a live local MongoDB (`clipcut_test`), drops it before and
  after each test — no mocking of the database layer. Only
  `render_engine.render_export` and `cloudinary_svc.enabled`/`project_dir`
  are monkeypatched (the same external-process/network boundaries
  `test_handler_transcribe.py` mocks). `worker_mod.run_once` and
  `jobs_mod.enqueue`/`request_cancel` run unmocked against the real queue
  collection, so claim/lease/cancel-flag logic is exercised for real.
- **`export` sub-document shape unchanged**: Verified by diff
  (`git show 395a0d1 -- backend/server.py`). The `processing` state set in
  `start_export` is byte-identical to before
  (`{"status": "processing", "progress": 0, "error": None, "stage": "cutting"}`).
  The `done` and `error` states written by the new handler are structurally
  identical to what `_run_export` wrote (same keys: status/progress/error/
  stage/path/meta/size/finished_at for done; status/progress/error/stage for
  error), just moved into `handlers/export.py`. The only externally visible
  change is an additive `job_id` field in the `POST /api/projects/{pid}/export`
  response body — not part of the `export` sub-document — which the brief
  explicitly specifies and which frontend code reading `reel_settings`/`ok`
  from that response is unaffected by.

## Concerns

None. The brief's assumptions about `server.py`'s current state (line
numbers, imports already present) matched the actual file exactly, so no
judgment calls were needed beyond following the brief verbatim.

## Step 6 — explicitly skipped

Per the controller's instruction, I did not attempt the brief's Step 6
(start the worker, upload a clip through the browser UI with a real
ElevenLabs key, and confirm the export completes and plays). This requires
a live browser session and a valid API key that this task run does not have.
The controller will verify end-to-end separately.

## Fix pass

A review of this task found three issues in `backend/handlers/export.py` and
its relationship to `backend/server.py`. All three are fixed in this pass.

### Finding 1 (Important) — worker imported the API layer

`handlers/export.py` did `from server import compute_cut_state` inside
`run()`. That lazy import pulled in all of `server.py`'s module-level code
(the FastAPI app, CORS middleware, a second unused `MongoClient`), inverting
the intended dependency direction (worker must not depend on the API) and
dragging FastAPI's upload machinery into the worker process — the visible
symptom was a starlette `PendingDeprecationWarning` about `python_multipart`
when running `tests/test_handler_export.py`.

**Fix:** created `backend/cut_state.py` and moved `compute_cut_state`,
`DEFAULT_CUT_SETTINGS`, `DEFAULT_REEL` into it unchanged (byte-identical
values and logic — only the module they live in changed). `cut_state.py`
imports only `cuts` and `zooms` (both already dependency-free of `server`),
so it carries no FastAPI/Mongo baggage.

- `backend/server.py` now does
  `from cut_state import DEFAULT_CUT_SETTINGS, DEFAULT_REEL, compute_cut_state, now_iso`
  at module level (added alongside the existing `import cloudinary_svc` /
  `import jobs` / `import render_engine` block) and every existing reference
  (`DEFAULT_CUT_SETTINGS` at doc-init, `DEFAULT_REEL` at doc-init and in
  `reel_settings` defaulting, `compute_cut_state` at the three call sites)
  keeps working unmodified since the names are imported into the same
  namespace. Also dropped `import cuts as cuts_mod` and `import zooms` from
  `server.py` (no longer referenced there — `cut_state.py` owns those calls
  now) and the now-dead `from datetime import datetime, timezone` import
  (its only use, `now_iso`, moved to `cut_state.py`).
- `backend/handlers/export.py` now does
  `from cut_state import compute_cut_state, now_iso` at module level and the
  in-function `from server import compute_cut_state` plus its
  "imported lazily; server owns cut math" comment are deleted.

### Finding 2 (Minor) — cancelling left the project doc stuck at "processing"

Before this fix, when `ctx.cancelled()` was true, `export.py` raised
`Cancelled` immediately; the worker correctly marked the *job* `cancelled`,
but the *project's* `export` sub-document was left at whatever
`start_export` last wrote (`{"status": "processing", ...}`) forever — the
frontend would show a stuck spinner.

**Fix:** in `handlers/export.py`, immediately before raising `Cancelled`,
the project's `export` sub-document is now set to
`{"status": "cancelled", "progress": 0, "error": None, "stage": "cancelled"}`
— the same four keys (`status`/`progress`/`error`/`stage`) the failure path
already writes, so the shape the frontend reads stays consistent.

`test_export_honours_cancellation_before_render` in
`backend/tests/test_handler_export.py` was extended with:

```python
exp = db.projects.find_one({"id": "p3"})["export"]
assert exp["status"] == "cancelled"
assert exp["progress"] == 0
assert exp["error"] is None
assert exp["stage"] == "cancelled"
```

### Finding 3 (Minor) — duplicated `_now_iso()` / `now_iso()`

`handlers/export.py` defined its own `_now_iso()`, duplicating
`server.py`'s `now_iso()`. Both had the identical body
(`datetime.now(timezone.utc).isoformat()`).

**Fix:** `now_iso` now lives in `backend/cut_state.py` (output format
unchanged — still `datetime.now(timezone.utc).isoformat()`). `server.py`
imports it from `cut_state` (see Finding 1's import line) instead of
defining it locally. `handlers/export.py` imports it the same way and its
call site (`"finished_at": _now_iso()`) was changed to `now_iso()`; the
local `_now_iso` def and the now-unused `from datetime import datetime,
timezone` import were deleted from `export.py`.

### Commands run

```
cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/ -v --ignore=tests/test_reel_backend.py --ignore=tests/test_backend_e2e.py
```

```
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0 -- D:\Desktop\Desktop Files\Projects\clipcut\.venv-local\Scripts\python.exe
cachedir: .pytest_cache
rootdir: D:\Desktop\Desktop Files\Projects\clipcut
configfile: pyproject.toml
plugins: anyio-4.14.2
collecting ... collected 24 items

tests\test_handler_export.py::test_export_writes_done_state PASSED       [  4%]
tests\test_handler_export.py::test_export_failure_records_error_not_stuck_processing PASSED [  8%]
tests\test_handler_export.py::test_export_honours_cancellation_before_render PASSED [ 12%]
tests\test_handler_transcribe.py::test_transcribe_stores_words_and_marks_ready PASSED [ 16%]
tests\test_handler_transcribe.py::test_transcribe_failure_marks_project_error PASSED [ 20%]
tests\test_jobs.py::test_enqueue_creates_queued_job PASSED               [ 25%]
tests\test_jobs.py::test_claim_returns_job_and_marks_processing PASSED   [ 29%]
tests\test_jobs.py::test_claim_is_atomic_second_caller_gets_nothing PASSED [ 33%]
tests\test_jobs.py::test_claim_ignores_other_kinds PASSED                [ 37%]
tests\test_jobs.py::test_reconcile_requeues_expired_lease PASSED         [ 41%]
tests\test_jobs.py::test_reconcile_fails_job_past_max_attempts PASSED    [ 45%]
tests\test_jobs.py::test_cancel_flag_roundtrip PASSED                    [ 50%]
tests\test_jobs.py::test_finish_and_fail_set_terminal_status PASSED      [ 54%]
tests\test_jobs.py::test_heartbeat_extends_lease PASSED                  [ 58%]
tests\test_jobs.py::test_heartbeat_returns_false_when_not_processing PASSED [ 62%]
tests\test_jobs.py::test_set_progress_updates_fields PASSED              [ 66%]
tests\test_jobs.py::test_cancel_sets_terminal_status PASSED              [ 70%]
tests\test_worker.py::test_run_once_returns_false_when_queue_empty PASSED [ 75%]
tests\test_worker.py::test_run_once_dispatches_and_finishes PASSED       [ 79%]
tests\test_worker.py::test_handler_exception_fails_the_job PASSED        [ 83%]
tests\test_worker.py::test_unknown_kind_fails_cleanly PASSED             [ 87%]
tests\test_worker.py::test_ctx_cancelled_reflects_flag PASSED            [ 91%]
tests\test_worker.py::test_handler_raising_cancelled_marks_job_cancelled PASSED [ 95%]
tests\test_worker.py::test_progress_warns_when_lease_lost PASSED         [100%]

============================= 24 passed in 1.31s ==============================
```

All 24 tests pass, no warnings emitted (compare to the pre-fix baseline of
"24 passed, 1 warning" recorded earlier in this report).

```
cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_export.py -v
```

```
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0 -- D:\Desktop\Desktop Files\Projects\clipcut\.venv-local\Scripts\python.exe
cachedir: .pytest_cache
rootdir: D:\Desktop\Desktop Files\Projects\clipcut
configfile: pyproject.toml
plugins: anyio-4.14.2
collecting ... collected 3 items

tests\test_handler_export.py::test_export_writes_done_state PASSED       [ 33%]
tests\test_handler_export.py::test_export_failure_records_error_not_stuck_processing PASSED [ 66%]
tests\test_handler_export.py::test_export_honours_cancellation_before_render PASSED [100%]

============================== 3 passed in 0.58s ==============================
```

### Starlette warning: gone

`tests/test_handler_export.py` run alone produces **"3 passed in 0.58s"**
with no warnings summary section at all — no
`PendingDeprecationWarning`/`python_multipart` line, versus the "3 passed,
1 warning in 1.26s" recorded for the original implementation earlier in
this report. `handlers/export.py` no longer imports `server` (checked with
`grep -rn "from server\|import server" backend` — zero matches outside this
report file), so FastAPI's upload machinery is never pulled into the worker
process. The dependency inversion is genuinely fixed, not just
warning-suppressed: there is no `filterwarnings` config in `pyproject.toml`
that could be hiding it.

### Files changed

- `backend/cut_state.py` (new) — `compute_cut_state`, `DEFAULT_CUT_SETTINGS`,
  `DEFAULT_REEL`, `now_iso`, all moved unchanged from `server.py` (plus
  `_now_iso`'s body, deduplicated into the one `now_iso`).
- `backend/server.py` (modified) — imports the four names from `cut_state`
  instead of defining them; dropped `import cuts as cuts_mod`, `import
  zooms`, and `from datetime import datetime, timezone` (all now unused).
- `backend/handlers/export.py` (modified) — module-level
  `from cut_state import compute_cut_state, now_iso`; deleted the lazy
  `from server import compute_cut_state` and its comment; deleted the local
  `_now_iso` def and its `datetime`/`timezone` import; cancellation branch
  now writes `{"status": "cancelled", "progress": 0, "error": None, "stage":
  "cancelled"}` to the project doc before raising `Cancelled`.
- `backend/tests/test_handler_export.py` (modified) —
  `test_export_honours_cancellation_before_render` extended to assert the
  project's `export` sub-document reaches `status == "cancelled"` (plus
  `progress`/`error`/`stage`).

### Concerns

None. `DEFAULT_CUT_SETTINGS`, `DEFAULT_REEL`, and `now_iso`'s output format
are unchanged (moved, not edited); every existing call site in `server.py`
was grepped and confirmed to still resolve via the new import; the full
covering test suite (24 tests) and the isolated `test_handler_export.py`
run both pass cleanly with zero warnings.
