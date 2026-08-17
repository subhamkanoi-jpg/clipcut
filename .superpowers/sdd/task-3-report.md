# Task 3 Report: Move transcription onto the queue

## Summary

Moved transcription off the `threading.Thread` daemon in `complete_upload` and
onto the durable Mongo job queue built in Tasks 1-2. Added a `backend/handlers/`
package with a `transcribe` handler that registers itself in `worker.HANDLERS`
on import, wired it into `worker.py`'s `_register_handlers()`, and replaced the
thread spawn + `_run_transcription` in `server.py` with `jobs.enqueue(db, pid,
"transcribe")`.

## What was implemented

- **`backend/handlers/__init__.py`** (new) — package marker docstring, as specified.
- **`backend/handlers/transcribe.py`** (new) — `run(ctx) -> dict`:
  - Loads the project doc by `ctx.project_id`; raises if missing.
  - Reports progress (`10, "transcribing"`).
  - Calls `transcription.transcribe_video(Path(doc["video_path"]))`.
  - On success: sets project `status="ready"`, stores `words`, `text`, clears
    `error`.
  - On failure: sets project `status="error"` with `error` (truncated to 500
    chars), then re-raises so the job itself is marked failed by
    `worker.run_once`.
  - Registers itself: `worker.HANDLERS["transcribe"] = run` at module scope.
- **`backend/worker.py`** (modified) — added `_register_handlers()` (imports
  `handlers.transcribe`, which performs the `HANDLERS` registration as a side
  effect of import) and calls it as the first statement of `main()`, per the
  brief's note that `HANDLERS` must exist before handler modules populate it
  and that this only matters for the standalone worker process (tests import
  `handlers.transcribe` directly, registering it for themselves).
- **`backend/server.py`** (modified):
  - Added `import jobs` to the flat top-level import block.
  - Removed the now-unused `import transcription` (its only remaining
    reference was inside the deleted `_run_transcription`).
  - Replaced `threading.Thread(target=_run_transcription, args=(pid,),
    daemon=True).start()` with `jobs.enqueue(db, pid, "transcribe")` at the
    end of `complete_upload`.
  - Deleted `_run_transcription` entirely.
- **`backend/tests/test_handler_transcribe.py`** (new) — the two tests exactly
  as given in the brief (`test_transcribe_stores_words_and_marks_ready`,
  `test_transcribe_failure_marks_project_error`), verbatim.

## TDD evidence

**RED** — `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_transcribe.py -v`

```
collecting ... collected 0 items / 1 error
ERROR collecting backend/tests/test_handler_transcribe.py
ImportError while importing test module ...
tests\test_handler_transcribe.py:12: in <module>
    import handlers.transcribe as th
E   ModuleNotFoundError: No module named 'handlers'
=========================== short test summary info ===========================
ERROR tests\test_handler_transcribe.py
1 error in 0.41s
```

Matches the brief's expected failure exactly.

**GREEN** — same command after implementation:

```
tests\test_handler_transcribe.py::test_transcribe_stores_words_and_marks_ready PASSED [ 50%]
tests\test_handler_transcribe.py::test_transcribe_failure_marks_project_error PASSED [100%]

============================== 2 passed in 0.39s ==============================
```

**Step 5 grep check** — `cd backend && grep -n "threading.Thread" server.py`

```
391:    threading.Thread(target=_run_export, args=(pid, body.caption_style, reel), daemon=True).start()
```

One remaining match, in `start_export` / `_run_export` — the export path, out
of scope for this task (Task 4). The transcription thread spawn is gone.

**Full backend suite** (excluding the two live-server e2e files per
instructions):

```
cd backend && ../.venv-local/Scripts/python.exe -m pytest --ignore=tests/test_reel_backend.py --ignore=tests/test_backend_e2e.py -q
.....................                                                    [100%]
21 passed in 1.00s
```

All 21 tests pass: 2 new handler tests, 12 existing `test_jobs.py`, 7 existing
`test_worker.py`. Also confirmed `import server` still succeeds standalone
(`python -c "import server"`) since none of the runnable tests import
`server.py` directly.

## Files changed

- `backend/handlers/__init__.py` (new)
- `backend/handlers/transcribe.py` (new)
- `backend/worker.py` (modified — `_register_handlers()` + call in `main()`)
- `backend/server.py` (modified — `import jobs`, removed unused `import
  transcription`, replaced thread spawn with `jobs.enqueue`, deleted
  `_run_transcription`)
- `backend/tests/test_handler_transcribe.py` (new)

Commit: `f551c67` — "feat: run transcription as a queued job" (5 files
changed, 100 insertions, 16 deletions). Staged explicit paths only
(`backend/handlers backend/worker.py backend/server.py
backend/tests/test_handler_transcribe.py`); confirmed via `git status --short`
before committing that no unrelated files were staged. Pre-existing unstaged
modifications to `.superpowers/sdd/progress.md` and various `task-*-brief.md`/
`task-1-report.md` files (present before I started, not touched by this task)
were left alone.

## Self-review

- **Completeness**: All six brief steps executed in order (write failing
  test, confirm RED, implement, confirm GREEN, grep check, commit). Interfaces
  consumed exactly as documented (`Ctx.progress`, `worker.HANDLERS`,
  `jobs.enqueue`, `transcription.transcribe_video`). Project document gets
  `status`, `words`, `text`, `error` fields as specified.
- **YAGNI**: No scope creep. The one deviation from the brief's literal diff
  is removing the now-dead `import transcription` line from `server.py` — a
  direct, zero-risk consequence of deleting `_run_transcription` (it was the
  only remaining reference). Left the export thread and `_run_export`
  untouched per "Export moves in Task 4 — leave it alone." Did not touch
  `jobs.py` or add any new job-queue functionality beyond what Tasks 1-2
  already provide.
- **Tests verify real behavior against real Mongo**: Yes. `test_handler_transcribe.py`
  uses a real `MongoClient` against `clipcut_test` (dropped before/after),
  performs a real `jobs.enqueue` (real insert), a real `worker.run_once`
  (real `find_one_and_update` claim, real handler dispatch, real `finish`/
  `fail` updates), and asserts against real `db.projects.find_one` /
  `db.jobs.find_one` reads. Only the external network boundary
  (`transcription.transcribe_video`, which would call ElevenLabs Scribe) is
  monkeypatched — correct isolation for a unit-level handler test, consistent
  with how `test_worker.py` mocks handler bodies but exercises the real queue
  underneath.
- **Style note**: `worker.py`'s `_register_handlers()` placement (no blank
  lines before/after the function def, sandwiched between `import jobs as
  jobs_mod` and `POLL_S = 1.0`) is copied verbatim from the brief rather than
  reformatted to PEP8 two-blank-line convention used elsewhere in the file.
  Kept as specified since the brief says its code is to be used verbatim;
  flagging in case a stricter lint pass is desired later.

## Concerns

None. No blockers, no ambiguity encountered — `server.py`'s actual code
matched what the brief described at the relevant lines (`complete_upload` /
`_run_transcription`), so no clarification was needed before starting.
