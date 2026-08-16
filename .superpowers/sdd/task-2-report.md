# Task 2 Report: Worker Process

## Status

**DONE**

## Summary

Implemented the ClipCut job worker process with handler registry, cancellation support, and comprehensive error handling. The worker consumes the Mongo-backed job queue from Task 1, claims jobs, executes registered handlers, and manages job lifecycle. All 5 new tests pass; all 11 pre-existing Task 1 tests continue to pass (16 total).

## Files Created

| Path | Role |
|------|------|
| `backend/worker.py` | Worker process: `Ctx`, `run_once()`, `main()`, `HANDLERS` registry |
| `backend/tests/test_worker.py` | 5 comprehensive tests covering happy path, errors, cancellation |

## Interfaces Produced

```python
@dataclass
class Ctx:
    db: object          # MongoDB database instance
    job: dict           # Job document from database
    project_id: str     # Project ID from job
    payload: dict       # Job-specific payload

    def progress(self, p: int, stage: str) -> None:
        """Update progress % and stage; extends lease via heartbeat."""

    def cancelled(self) -> bool:
        """Check if cancellation was requested."""

HANDLERS: dict[str, Callable[[Ctx], dict]]  # Handler registry

def run_once(db, worker_id: str) -> bool:
    """Claim one job from queue, execute handler, return True if a job was processed."""

def main() -> None:
    """Worker process entry point. Connects to Mongo, reconciles stale jobs, polls queue."""
```

## Interfaces Consumed

From `backend/jobs.py` (Task 1):
- `claim(db, kinds, worker_id, lease_s=60) -> dict | None`
- `heartbeat(db, job_id, lease_s=60) -> bool`
- `finish(db, job_id, result=None) -> None`
- `fail(db, job_id, error) -> None`
- `set_progress(db, job_id, progress, stage) -> None`
- `request_cancel(db, job_id) -> None`
- `is_cancelled(db, job_id) -> bool`
- `reconcile_stale(db) -> int`

## Implementation Details

### Job Claiming Strategy
The `run_once()` function discovers all queued job kinds dynamically:
1. Queries `db.jobs.distinct("kind", {"status": "queued"})` to find all pending kinds
2. Constructs claims list: registered handlers + unregistered queued kinds
3. Calls `jobs_mod.claim()` with comprehensive list
4. Falls back to `["__none__"]` if no jobs exist

This approach allows graceful handling of jobs with unknown/unregistered kinds—they get claimed and failed with "no handler" message rather than permanently stuck in queue.

### Handler Dispatch
- `HANDLERS` is a plain dict initialized at module level (empty)
- Handler modules import `worker` and mutate `HANDLERS` to register themselves
- The `main()` function will call `_register_handlers()` (implementation deferred to Tasks 3-4)
- Tests inject test handlers directly into `HANDLERS` dict
- Unknown kinds receive clear error: `"no handler for kind 'xyz'"`

### Error Handling
- Handler exceptions caught and logged
- Job marked as failed with exception message (truncated to 2000 chars)
- Worker loop continues on exception; sleeps 1 second before next poll
- Worker responds to SIGINT/SIGTERM gracefully

### Cancellation Support
- `Ctx.cancelled()` checks database flag set by `jobs_mod.request_cancel()`
- Handlers can poll during long work and decide to exit
- Built-in `Cancelled` exception available for programmatic exits
- Cancelled jobs marked with status `"cancelled"` and stage `"cancelled"`

### Worker Lifecycle
1. Connect to MongoDB using `MONGO_URL` and `DB_NAME` env vars
2. Generate worker_id as `hostname-pid` for fleet identification
3. Call `jobs_mod.reconcile_stale(db)` to requeue expired leases on boot
4. Log ready state with handler list
5. Poll queue every 1 second via `run_once(db, worker_id)`
6. Respond to SIGINT/SIGTERM by setting `_shutdown` flag
7. Exit cleanly and log stop

## TDD Evidence

### Step 2: RED - Test Fails
```
ERROR tests/test_worker.py
ModuleNotFoundError: No module named 'worker'
====== 1 error during collection ======
```

### Step 4: GREEN - All Tests Pass
```
tests/test_worker.py::test_run_once_returns_false_when_queue_empty PASSED [ 20%]
tests/test_worker.py::test_run_once_dispatches_and_finishes PASSED       [ 40%]
tests/test_worker.py::test_handler_exception_fails_the_job PASSED        [ 60%]
tests/test_worker.py::test_unknown_kind_fails_cleanly PASSED             [ 80%]
tests/test_worker.py::test_ctx_cancelled_reflects_flag PASSED            [100%]

============================= 5 passed in 0.30s ==============================
```

### Full Suite Verification
```
tests/test_jobs.py::test_enqueue_creates_queued_job PASSED               [  6%]
tests/test_jobs.py::test_claim_returns_job_and_marks_processing PASSED   [ 12%]
tests/test_jobs.py::test_claim_is_atomic_second_caller_gets_nothing PASSED [ 18%]
tests/test_jobs.py::test_claim_ignores_other_kinds PASSED                [ 25%]
tests/test_jobs.py::test_reconcile_requeues_expired_lease PASSED         [ 31%]
tests/test_jobs.py::test_reconcile_fails_job_past_max_attempts PASSED    [ 37%]
tests/test_jobs.py::test_cancel_flag_roundtrip PASSED                    [ 43%]
tests/test_jobs.py::test_finish_and_fail_set_terminal_status PASSED      [ 50%]
tests/test_jobs.py::test_heartbeat_extends_lease PASSED                  [ 56%]
tests/test_jobs.py::test_heartbeat_returns_false_when_not_processing PASSED [ 62%]
tests/test_jobs.py::test_set_progress_updates_fields PASSED              [ 68%]
tests/test_worker.py::test_run_once_returns_false_when_queue_empty PASSED [ 75%]
tests/test_worker.py::test_run_once_dispatches_and_finishes PASSED       [ 81%]
tests/test_worker.py::test_handler_exception_fails_the_job PASSED        [ 87%]
tests/test_worker.py::test_unknown_kind_fails_cleanly PASSED             [ 93%]
tests/test_worker.py::test_ctx_cancelled_reflects_flag PASSED            [100%]

============================= 16 passed in 0.58s ========================
```

**5/5 new tests pass; 16/16 total (no regressions).**

## Test Coverage

### Test Scenarios Verified Against Real MongoDB

1. **Empty Queue** (`test_run_once_returns_false_when_queue_empty`)
   - Queue empty → `run_once` returns False
   - Worker properly backs off (would sleep 1s in production)

2. **Happy Path** (`test_run_once_dispatches_and_finishes`)
   - Handler registered in `HANDLERS`
   - Job claimed, executed, progress updated, result returned
   - Job marked "done" with result in database
   - Handler receives correct `Ctx` with project_id and payload

3. **Handler Exception** (`test_handler_exception_fails_the_job`)
   - Handler raises RuntimeError
   - Exception caught and logged
   - Job marked "error" with exception message in database
   - Worker continues running (doesn't crash)

4. **Unknown Handler Kind** (`test_unknown_kind_fails_cleanly`)
   - Job enqueued with kind not in `HANDLERS`
   - Job still claimed despite unknown kind
   - Job marked "error" with "no handler" message
   - Graceful degradation (no permanent stuck jobs)

5. **Cancellation Flag** (`test_ctx_cancelled_reflects_flag`)
   - Handler can query cancellation status via `ctx.cancelled()`
   - Before request: returns False
   - After `jobs_mod.request_cancel()`: returns True
   - Handler can react in real-time

All tests:
- Use real MongoClient connecting to localhost:27017
- Operate on isolated `clipcut_test` database
- Drop/recreate database per test for isolation
- Assert on actual job documents in database (not mocks)
- Verify end-to-end job lifecycle

## Commit

- **SHA**: `6920250`
- **Subject**: `feat: add job worker with handler registry and cancellation`
- **Branch**: `feat/clipcut-foundation` (stayed on branch, no merge/rebase)
- **Staged files**: `backend/worker.py`, `backend/tests/test_worker.py` only
- **Verification**: `git status --short` before commit showed only intended files

## Self-Review

### Completeness
- [x] `Ctx` dataclass with all required fields
- [x] `Ctx.progress(p, stage)` method
- [x] `Ctx.cancelled()` method
- [x] `HANDLERS` registry dict
- [x] `run_once(db, worker_id)` function returning bool
- [x] `main()` entry point with Mongo connection, reconciliation, signal handling, poll loop
- [x] All imports flat (no package-relative imports)
- [x] All 5 tests from brief implemented exactly

### Quality
- No external dependencies beyond existing (pymongo, python-dotenv)
- Proper error handling: exceptions caught, logged, job failed
- Defensive against edge cases: empty handlers, unknown kinds, cancellation during execution
- Signal handlers prevent abrupt shutdown
- Logging configured and informative
- Worker ID generation for fleet identification

### YAGNI (You Aren't Gonna Need It)
- No pre-built features for transcription or export handlers (Tasks 3-4)
- No optimization premature (kind discovery query acceptable for 1-2 calls/sec)
- Just the foundation: claim loop, context object, registry

### Testing Against Real Mongo (not mocks)
- Tests use real MongoClient
- Real `clipcut_test` database with real documents
- `jobs_mod` not mocked—real implementations called
- Database state assertions after operations prove actual behavior
- No test doubles or stubs

### Concerns Addressed
1. **Kind discovery overhead**: `db.jobs.distinct()` per claim call
   - Acceptable at typical worker load (1-2 calls/sec)
   - Could be optimized later if needed (e.g., cached list)
   - Necessary for unknown kind handling

2. **Cancelled exception unused in tests**: By design
   - Code structure supports it for future handlers
   - Primary path is normal exception + fail
   - Ready for use in Tasks 3-4

3. **Handler registration not tested**: By design per brief
   - `_register_handlers()` deferred to Tasks 3-4
   - Tests inject handlers directly into `HANDLERS`
   - Ordering (HANDLERS exists before imports) documented and preserved

## No Regressions
All 11 Task 1 tests from `test_jobs.py` continue to pass unchanged.

## Files Changed
- **Created**: `backend/worker.py` (207 lines of implementation)
- **Created**: `backend/tests/test_worker.py` (83 lines of tests)

## Fix pass

Three review findings against Task 2 (commit `6920250`), all fixed in one pass.

### Finding 1 (Important): cancellation bypassed jobs.py helpers

- Added `cancel(db, job_id)` to `backend/jobs.py`, matching the style of
  `finish()`/`fail()`: sets `status: "cancelled"`, `stage: "cancelled"`, and
  `finished_at` (via the module's `_now()`).
- `backend/worker.py`'s `run_once()` `except Cancelled:` handler now calls
  `jobs_mod.cancel(db, job["id"])` instead of a raw `db.jobs.update_one(...)`
  that only set `status`/`stage` and never `finished_at`.
- Added `test_cancel_sets_terminal_status` to `backend/tests/test_jobs.py`:
  asserts `status == "cancelled"`, `stage == "cancelled"`, `finished_at is not None`.
- Added `test_handler_raising_cancelled_marks_job_cancelled` to
  `backend/tests/test_worker.py`: registers a dummy handler that raises
  `worker.Cancelled`, runs `run_once`, asserts the job doc has
  `status == "cancelled"` and `finished_at is not None`.

### Finding 2 (Important): lost lease was silently ignored

- `Ctx.progress()` in `backend/worker.py` now captures the return value of
  `jobs_mod.heartbeat(...)`. When it is `False`, logs
  `log.warning("lost lease on job %s; another worker may have reclaimed it", self.job["id"])`.
  The handler is **not** aborted and no exception is raised — per the brief,
  detection/visibility is the goal, not stopping a long-running ffmpeg export
  for a race that needs two concurrent worker processes.
- Added `test_progress_warns_when_lease_lost` to `backend/tests/test_worker.py`
  using `caplog`: enqueues and claims a job (giving it `status: "processing"`),
  then forces `status` back to `"queued"` directly via `db.jobs.update_one`
  (simulating the lease having been lost/reclaimed), builds a `Ctx` around that
  stale job, calls `ctx.progress(10, "working")` inside
  `caplog.at_level(logging.WARNING, logger="worker")`, and asserts
  `f"lost lease on job {jid}"` appears in `caplog.text`.

### Finding 3 (Minor): dead constant

- Removed `HEARTBEAT_S = 5.0` from `backend/worker.py`. It was never
  referenced anywhere (heartbeats fire on every `Ctx.progress()` call, not on
  a 5s cadence), so the constant was misleading. No test needed — its removal
  is verified by the module still importing cleanly and all tests passing.

### Command run

```
cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_jobs.py tests/test_worker.py -v
```

### Actual output

```
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0 -- D:\Desktop\Desktop Files\Projects\clipcut\.venv-local\Scripts\python.exe
cachedir: .pytest_cache
rootdir: D:\Desktop\Desktop Files\Projects\clipcut
configfile: pyproject.toml
plugins: anyio-4.14.2
collecting ... collected 19 items

tests\test_jobs.py::test_enqueue_creates_queued_job PASSED               [  5%]
tests\test_jobs.py::test_claim_returns_job_and_marks_processing PASSED   [ 10%]
tests\test_jobs.py::test_claim_is_atomic_second_caller_gets_nothing PASSED [ 15%]
tests\test_jobs.py::test_claim_ignores_other_kinds PASSED                [ 21%]
tests\test_jobs.py::test_reconcile_requeues_expired_lease PASSED         [ 26%]
tests\test_jobs.py::test_reconcile_fails_job_past_max_attempts PASSED    [ 31%]
tests\test_jobs.py::test_cancel_flag_roundtrip PASSED                    [ 36%]
tests\test_jobs.py::test_finish_and_fail_set_terminal_status PASSED      [ 42%]
tests\test_jobs.py::test_heartbeat_extends_lease PASSED                  [ 47%]
tests\test_jobs.py::test_heartbeat_returns_false_when_not_processing PASSED [ 52%]
tests\test_jobs.py::test_set_progress_updates_fields PASSED              [ 57%]
tests\test_jobs.py::test_cancel_sets_terminal_status PASSED              [ 63%]
tests\test_worker.py::test_run_once_returns_false_when_queue_empty PASSED [ 68%]
tests\test_worker.py::test_run_once_dispatches_and_finishes PASSED       [ 73%]
tests\test_worker.py::test_handler_exception_fails_the_job PASSED        [ 78%]
tests\test_worker.py::test_unknown_kind_fails_cleanly PASSED             [ 84%]
tests\test_worker.py::test_ctx_cancelled_reflects_flag PASSED            [ 89%]
tests\test_worker.py::test_handler_raising_cancelled_marks_job_cancelled PASSED [ 94%]
tests\test_worker.py::test_progress_warns_when_lease_lost PASSED         [100%]

============================= 19 passed in 0.95s ==============================
```

**19/19 passed (12 in `test_jobs.py`, 7 in `test_worker.py`); no warnings, no skips, no regressions.**

### Files touched in this pass

- `backend/jobs.py` — added `cancel(db, job_id)`
- `backend/worker.py` — `Cancelled` handler now calls `jobs_mod.cancel(...)`;
  `Ctx.progress()` logs a warning on lost lease; removed dead `HEARTBEAT_S`
- `backend/tests/test_jobs.py` — added `test_cancel_sets_terminal_status`
- `backend/tests/test_worker.py` — added
  `test_handler_raising_cancelled_marks_job_cancelled` and
  `test_progress_warns_when_lease_lost`

### Commit

- **Branch**: `feat/clipcut-foundation` (stayed on branch, no merge/rebase)
- **Staged files**: `backend/jobs.py`, `backend/worker.py`,
  `backend/tests/test_jobs.py`, `backend/tests/test_worker.py`,
  `.superpowers/sdd/task-2-report.md` only (explicit paths; verified with
  `git status --short` before committing — other unrelated modified files in
  `.superpowers/sdd/` from outside this fix pass were left untouched/unstaged)
