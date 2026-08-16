# ClipCut Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move ClipCut off daemon threads onto a durable Mongo-backed job queue, introduce the EDL v2 plan model, and converge on a single renderer — with zero user-visible change in behaviour.

**Architecture:** Three processes: an API that only reads/writes Mongo and enqueues jobs, a separate worker that runs all ffmpeg and transcription work, and Mongo holding projects, plans, and the queue. The worker inserts `helpers/` on `sys.path` so the `video-use` renderer becomes importable from the web app. Mongo is the source of truth; `data/<pid>/edit/` is a derived directory materialized for `helpers/` to consume.

**Tech Stack:** Python 3.12, FastAPI, pymongo, ffmpeg/libass, pytest. No new system services.

This plan implements steps 1-3 of the implementation order in
`docs/superpowers/specs/2026-08-17-clipcut-auto-editor-design.md`. Plan 2 covers
the decision providers, plan-review UI, and b-roll.

## Global Constraints

- Python 3.12. The venv is `.venv-local/` at the repo root; run everything as `../.venv-local/Scripts/python.exe` from `backend/`.
- Backend modules use **flat imports** (`import cuts`), so `backend/` must be the cwd. Do not convert to package-relative imports.
- `helpers/` is imported by inserting its directory on `sys.path`, never as a package. Its modules cross-import flatly (`from edl import ...`). Verified: zero filename collisions between `backend/*.py` and `helpers/*.py`.
- No new system services. Mongo (already installed, `mongodb://localhost:27017`) backs the job queue. No Redis.
- On Windows, spawn child processes via `helpers/hidden_proc.py:run` to avoid console-window flashes.
- Mongo is the source of truth. `data/<pid>/edit/` is derived and must be safe to delete and rebuild at any time.
- Output aspect is `9:16` primary; `original` remains a passthrough.
- Every code path must work with no LLM available.
- Tests use a separate database, `clipcut_test`, and drop it on teardown. Never point tests at `clipcut`.

---

### Task 1: Job queue primitives

**Files:**
- Create: `backend/jobs.py`
- Create: `backend/tests/test_jobs.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `enqueue(db, project_id, kind, payload=None) -> str`, `claim(db, kinds, worker_id, lease_s=60) -> dict | None`, `heartbeat(db, job_id, lease_s=60) -> bool`, `set_progress(db, job_id, progress, stage)`, `finish(db, job_id, result=None)`, `fail(db, job_id, error)`, `request_cancel(db, job_id)`, `is_cancelled(db, job_id) -> bool`, `reconcile_stale(db) -> int`. All take a pymongo `Database` as the first argument.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_jobs.py`:

```python
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs as jobs_mod


@pytest.fixture
def db():
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    name = "clipcut_test"
    client.drop_database(name)
    yield client[name]
    client.drop_database(name)


def test_enqueue_creates_queued_job(db):
    jid = jobs_mod.enqueue(db, "proj1", "render")
    doc = db.jobs.find_one({"id": jid})
    assert doc["status"] == "queued"
    assert doc["project_id"] == "proj1"
    assert doc["kind"] == "render"
    assert doc["attempts"] == 0


def test_claim_returns_job_and_marks_processing(db):
    jid = jobs_mod.enqueue(db, "proj1", "render")
    got = jobs_mod.claim(db, ["render"], "worker-a")
    assert got["id"] == jid
    assert got["status"] == "processing"
    assert got["attempts"] == 1
    assert db.jobs.find_one({"id": jid})["lease_expires_at"] is not None


def test_claim_is_atomic_second_caller_gets_nothing(db):
    jobs_mod.enqueue(db, "proj1", "render")
    first = jobs_mod.claim(db, ["render"], "worker-a")
    second = jobs_mod.claim(db, ["render"], "worker-b")
    assert first is not None
    assert second is None


def test_claim_ignores_other_kinds(db):
    jobs_mod.enqueue(db, "proj1", "transcribe")
    assert jobs_mod.claim(db, ["render"], "worker-a") is None


def test_reconcile_requeues_expired_lease(db):
    jid = jobs_mod.enqueue(db, "proj1", "render")
    jobs_mod.claim(db, ["render"], "worker-a")
    db.jobs.update_one(
        {"id": jid},
        {"$set": {"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=5)}},
    )
    assert jobs_mod.reconcile_stale(db) == 1
    assert db.jobs.find_one({"id": jid})["status"] == "queued"


def test_reconcile_fails_job_past_max_attempts(db):
    jid = jobs_mod.enqueue(db, "proj1", "render")
    db.jobs.update_one(
        {"id": jid},
        {"$set": {
            "status": "processing",
            "attempts": 3,
            "lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=5),
        }},
    )
    jobs_mod.reconcile_stale(db)
    doc = db.jobs.find_one({"id": jid})
    assert doc["status"] == "error"
    assert "attempts" in doc["error"]


def test_cancel_flag_roundtrip(db):
    jid = jobs_mod.enqueue(db, "proj1", "render")
    assert jobs_mod.is_cancelled(db, jid) is False
    jobs_mod.request_cancel(db, jid)
    assert jobs_mod.is_cancelled(db, jid) is True


def test_finish_and_fail_set_terminal_status(db):
    a = jobs_mod.enqueue(db, "p", "render")
    jobs_mod.finish(db, a, {"path": "out.mp4"})
    assert db.jobs.find_one({"id": a})["status"] == "done"

    b = jobs_mod.enqueue(db, "p", "render")
    jobs_mod.fail(db, b, "boom")
    doc = db.jobs.find_one({"id": b})
    assert doc["status"] == "error"
    assert doc["error"] == "boom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobs'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/jobs.py`:

```python
"""Durable job queue over Mongo. No external broker."""

import uuid
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument

MAX_ATTEMPTS = 3
DEFAULT_LEASE_S = 60


def _now():
    return datetime.now(timezone.utc)


def enqueue(db, project_id: str, kind: str, payload: dict | None = None) -> str:
    jid = str(uuid.uuid4())
    db.jobs.insert_one({
        "id": jid,
        "project_id": project_id,
        "kind": kind,
        "payload": payload or {},
        "status": "queued",
        "stage": None,
        "progress": 0,
        "attempts": 0,
        "max_attempts": MAX_ATTEMPTS,
        "lease_expires_at": None,
        "heartbeat_at": None,
        "cancel_requested": False,
        "error": None,
        "result": None,
        "created_at": _now(),
    })
    return jid


def claim(db, kinds: list, worker_id: str, lease_s: int = DEFAULT_LEASE_S) -> dict | None:
    now = _now()
    return db.jobs.find_one_and_update(
        {"status": "queued", "kind": {"$in": list(kinds)}},
        {
            "$set": {
                "status": "processing",
                "worker_id": worker_id,
                "lease_expires_at": now + timedelta(seconds=lease_s),
                "heartbeat_at": now,
                "started_at": now,
            },
            "$inc": {"attempts": 1},
        },
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


def heartbeat(db, job_id: str, lease_s: int = DEFAULT_LEASE_S) -> bool:
    now = _now()
    res = db.jobs.update_one(
        {"id": job_id, "status": "processing"},
        {"$set": {
            "heartbeat_at": now,
            "lease_expires_at": now + timedelta(seconds=lease_s),
        }},
    )
    return res.matched_count == 1


def set_progress(db, job_id: str, progress: int, stage: str) -> None:
    db.jobs.update_one(
        {"id": job_id},
        {"$set": {"progress": int(progress), "stage": stage}},
    )


def finish(db, job_id: str, result: dict | None = None) -> None:
    db.jobs.update_one({"id": job_id}, {"$set": {
        "status": "done",
        "progress": 100,
        "stage": "done",
        "result": result or {},
        "finished_at": _now(),
    }})


def fail(db, job_id: str, error: str) -> None:
    db.jobs.update_one({"id": job_id}, {"$set": {
        "status": "error",
        "stage": "failed",
        "error": str(error)[:2000],
        "finished_at": _now(),
    }})


def request_cancel(db, job_id: str) -> None:
    db.jobs.update_one({"id": job_id}, {"$set": {"cancel_requested": True}})


def is_cancelled(db, job_id: str) -> bool:
    doc = db.jobs.find_one({"id": job_id}, {"cancel_requested": 1})
    return bool(doc and doc.get("cancel_requested"))


def reconcile_stale(db) -> int:
    """Requeue processing jobs whose lease expired. Fail those past max attempts.

    Called on worker boot. Returns how many were requeued.
    """
    now = _now()
    requeued = 0
    for doc in db.jobs.find({"status": "processing", "lease_expires_at": {"$lt": now}}):
        if doc.get("attempts", 0) >= doc.get("max_attempts", MAX_ATTEMPTS):
            fail(db, doc["id"], f"exceeded max attempts ({doc.get('attempts')})")
            continue
        db.jobs.update_one({"id": doc["id"]}, {"$set": {
            "status": "queued",
            "lease_expires_at": None,
            "worker_id": None,
        }})
        requeued += 1
    return requeued
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_jobs.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/jobs.py backend/tests/test_jobs.py
git commit -m "feat: add Mongo-backed durable job queue"
```

---

### Task 2: Worker process

**Files:**
- Create: `backend/worker.py`
- Create: `backend/tests/test_worker.py`

**Interfaces:**
- Consumes: `jobs.claim`, `jobs.heartbeat`, `jobs.finish`, `jobs.fail`, `jobs.reconcile_stale`, `jobs.set_progress` from Task 1.
- Produces: `HANDLERS: dict[str, Callable[[Ctx], dict]]` registry, `Ctx` dataclass with fields `db`, `job`, `project_id`, `payload`, and methods `progress(p: int, stage: str)` and `cancelled() -> bool`; `run_once(db, worker_id) -> bool` (True if a job was processed); `main()`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_worker.py`:

```python
import os
import sys

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs as jobs_mod
import worker as worker_mod


@pytest.fixture
def db():
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    name = "clipcut_test"
    client.drop_database(name)
    yield client[name]
    client.drop_database(name)


def test_run_once_returns_false_when_queue_empty(db):
    assert worker_mod.run_once(db, "w1") is False


def test_run_once_dispatches_and_finishes(db):
    seen = {}

    def handler(ctx):
        seen["project_id"] = ctx.project_id
        ctx.progress(50, "working")
        return {"ok": True}

    worker_mod.HANDLERS["dummy"] = handler
    try:
        jid = jobs_mod.enqueue(db, "proj1", "dummy")
        assert worker_mod.run_once(db, "w1") is True
        doc = db.jobs.find_one({"id": jid})
        assert doc["status"] == "done"
        assert doc["result"] == {"ok": True}
        assert seen["project_id"] == "proj1"
    finally:
        del worker_mod.HANDLERS["dummy"]


def test_handler_exception_fails_the_job(db):
    def handler(ctx):
        raise RuntimeError("kaboom")

    worker_mod.HANDLERS["dummy"] = handler
    try:
        jid = jobs_mod.enqueue(db, "proj1", "dummy")
        worker_mod.run_once(db, "w1")
        doc = db.jobs.find_one({"id": jid})
        assert doc["status"] == "error"
        assert "kaboom" in doc["error"]
    finally:
        del worker_mod.HANDLERS["dummy"]


def test_unknown_kind_fails_cleanly(db):
    jid = jobs_mod.enqueue(db, "proj1", "no-such-kind")
    worker_mod.run_once(db, "w1")
    doc = db.jobs.find_one({"id": jid})
    assert doc["status"] == "error"
    assert "no handler" in doc["error"]


def test_ctx_cancelled_reflects_flag(db):
    observed = {}

    def handler(ctx):
        observed["before"] = ctx.cancelled()
        jobs_mod.request_cancel(ctx.db, ctx.job["id"])
        observed["after"] = ctx.cancelled()
        return {}

    worker_mod.HANDLERS["dummy"] = handler
    try:
        jobs_mod.enqueue(db, "proj1", "dummy")
        worker_mod.run_once(db, "w1")
        assert observed == {"before": False, "after": True}
    finally:
        del worker_mod.HANDLERS["dummy"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'worker'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/worker.py`:

```python
"""ClipCut job worker. Runs all long work; the API never does.

Run from the backend/ directory:
    ../.venv-local/Scripts/python.exe worker.py
"""

import logging
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

# helpers/ modules cross-import flatly, so the directory itself goes on the path.
HELPERS = ROOT.parent / "helpers"
if str(HELPERS) not in sys.path:
    sys.path.insert(0, str(HELPERS))

from pymongo import MongoClient

import jobs as jobs_mod

POLL_S = 1.0
HEARTBEAT_S = 5.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("worker")

_shutdown = False


@dataclass
class Ctx:
    db: object
    job: dict
    project_id: str
    payload: dict

    def progress(self, p: int, stage: str) -> None:
        jobs_mod.set_progress(self.db, self.job["id"], p, stage)
        jobs_mod.heartbeat(self.db, self.job["id"])

    def cancelled(self) -> bool:
        return jobs_mod.is_cancelled(self.db, self.job["id"])


class Cancelled(Exception):
    """Raised by a handler when it notices ctx.cancelled()."""


HANDLERS: dict = {}


def run_once(db, worker_id: str) -> bool:
    job = jobs_mod.claim(db, list(HANDLERS.keys()) or ["__none__"], worker_id)
    if not job:
        return False
    handler = HANDLERS.get(job["kind"])
    if handler is None:
        jobs_mod.fail(db, job["id"], f"no handler for kind {job['kind']!r}")
        return True
    ctx = Ctx(db=db, job=job, project_id=job["project_id"], payload=job.get("payload") or {})
    try:
        result = handler(ctx)
        jobs_mod.finish(db, job["id"], result or {})
    except Cancelled:
        db.jobs.update_one({"id": job["id"]}, {"$set": {
            "status": "cancelled", "stage": "cancelled",
        }})
        log.info("job %s cancelled", job["id"])
    except Exception as e:
        log.exception("job %s failed", job["id"])
        jobs_mod.fail(db, job["id"], str(e))
    return True


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    log.info("shutdown requested")


def main() -> None:
    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    worker_id = f"{socket.gethostname()}-{os.getpid()}"

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    requeued = jobs_mod.reconcile_stale(db)
    if requeued:
        log.info("requeued %d stale job(s)", requeued)
    log.info("worker %s ready, handlers: %s", worker_id, sorted(HANDLERS))

    while not _shutdown:
        try:
            if not run_once(db, worker_id):
                time.sleep(POLL_S)
        except Exception:
            log.exception("worker loop error")
            time.sleep(POLL_S)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_worker.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/worker.py backend/tests/test_worker.py
git commit -m "feat: add job worker with handler registry and cancellation"
```

---

### Task 3: Move transcription onto the queue

**Files:**
- Create: `backend/handlers/__init__.py`
- Create: `backend/handlers/transcribe.py`
- Modify: `backend/worker.py` (import the handler module so it registers)
- Modify: `backend/server.py:200-222` (replace `threading.Thread` with `jobs.enqueue`; delete `_run_transcription`)
- Create: `backend/tests/test_handler_transcribe.py`

**Interfaces:**
- Consumes: `Ctx`, `HANDLERS` from Task 2; `transcription.transcribe_video` (existing).
- Produces: `handlers.transcribe.run(ctx) -> dict` registered under kind `"transcribe"`. Sets the project's `status` to `ready` or `error` and stores `words` and `text`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_handler_transcribe.py`:

```python
import os
import sys
from pathlib import Path

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs as jobs_mod
import worker as worker_mod
import handlers.transcribe as th


@pytest.fixture
def db():
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    name = "clipcut_test"
    client.drop_database(name)
    yield client[name]
    client.drop_database(name)


def test_transcribe_stores_words_and_marks_ready(db, monkeypatch, tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"not-a-real-video")
    db.projects.insert_one({
        "id": "p1", "status": "transcribing", "video_path": str(video),
    })
    monkeypatch.setattr(
        th.transcription, "transcribe_video",
        lambda path: {"words": [{"text": "hi", "start": 0.0, "end": 0.3, "type": "word"}],
                      "text": "hi"},
    )
    jid = jobs_mod.enqueue(db, "p1", "transcribe")
    worker_mod.run_once(db, "w1")

    doc = db.projects.find_one({"id": "p1"})
    assert doc["status"] == "ready"
    assert doc["text"] == "hi"
    assert len(doc["words"]) == 1
    assert db.jobs.find_one({"id": jid})["status"] == "done"


def test_transcribe_failure_marks_project_error(db, monkeypatch, tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"x")
    db.projects.insert_one({
        "id": "p2", "status": "transcribing", "video_path": str(video),
    })

    def boom(path):
        raise RuntimeError("Scribe returned 401")

    monkeypatch.setattr(th.transcription, "transcribe_video", boom)
    jobs_mod.enqueue(db, "p2", "transcribe")
    worker_mod.run_once(db, "w1")

    doc = db.projects.find_one({"id": "p2"})
    assert doc["status"] == "error"
    assert "401" in doc["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_transcribe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handlers'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/handlers/__init__.py`:

```python
"""Job handlers. Importing a module here registers it in worker.HANDLERS."""
```

Create `backend/handlers/transcribe.py`:

```python
"""Transcription job: Scribe -> words on the project document."""

from pathlib import Path

import transcription
import worker


def run(ctx) -> dict:
    doc = ctx.db.projects.find_one({"id": ctx.project_id})
    if not doc:
        raise RuntimeError(f"project {ctx.project_id} not found")
    ctx.progress(10, "transcribing")
    try:
        payload = transcription.transcribe_video(Path(doc["video_path"]))
    except Exception as e:
        ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
            "status": "error", "error": str(e)[:500],
        }})
        raise
    words = payload.get("words") or []
    ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
        "status": "ready",
        "words": words,
        "text": payload.get("text") or "",
        "error": None,
    }})
    return {"word_count": len(words)}


worker.HANDLERS["transcribe"] = run
```

Modify `backend/worker.py` — add below the `import jobs as jobs_mod` line:

```python
import jobs as jobs_mod

# Importing handler modules registers them in HANDLERS. Keep after HANDLERS exists.
def _register_handlers() -> None:
    import handlers.transcribe  # noqa: F401
```

and call `_register_handlers()` as the first statement inside `main()`.

Note: `HANDLERS` must be defined before `_register_handlers` runs, which is why
registration happens inside `main()` rather than at import time. Tests import
`handlers.transcribe` directly, which registers it for them.

Modify `backend/server.py` — delete the `_run_transcription` function
(lines 208-222) and replace the thread spawn at the end of `complete_upload`:

```python
    # was: threading.Thread(target=_run_transcription, args=(pid,), daemon=True).start()
    jobs.enqueue(db, pid, "transcribe")
    return {"ok": True, "status": "transcribing", "duration": info["duration"]}
```

Add `import jobs` to the imports at the top of `backend/server.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_transcribe.py -v`
Expected: PASS, 2 passed

- [ ] **Step 5: Verify the API no longer spawns threads**

Run: `cd backend && grep -n "threading.Thread" server.py`
Expected: one remaining match, in `start_export` — removed in Task 4.

- [ ] **Step 6: Commit**

```bash
git add backend/handlers backend/worker.py backend/server.py backend/tests/test_handler_transcribe.py
git commit -m "feat: run transcription as a queued job"
```

---

### Task 4: Move export onto the queue

**Files:**
- Create: `backend/handlers/export.py`
- Modify: `backend/worker.py:_register_handlers`
- Modify: `backend/server.py:385-459` (`start_export` enqueues; delete `_run_export`)
- Modify: `backend/server.py` (add `POST /api/jobs/{jid}/cancel`, `GET /api/jobs/{jid}`)
- Create: `backend/tests/test_handler_export.py`

**Interfaces:**
- Consumes: `Ctx` from Task 2; `render_engine.render_export`, `cloudinary_svc.enabled`, `cloudinary_svc.upload_reel`, `compute_cut_state` (all existing).
- Produces: `handlers.export.run(ctx) -> dict` under kind `"export"`. Reads `ctx.payload` keys `caption_style` and `reel`. Writes the project's `export` sub-document exactly as `_run_export` did today, so the frontend needs no change.

This task deliberately keeps `render_engine.render_export` as the renderer. Swapping
renderers happens in Task 12, after the EDL model exists. One behaviour change at a
time.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_handler_export.py`:

```python
import os
import sys

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs as jobs_mod
import worker as worker_mod
import handlers.export as eh

REEL = {
    "aspect": "9:16", "cinematic": True, "karaoke": True, "zoom_intensity": 1.0,
    "punch_ins": True, "punch_sensitivity": 0.5, "burn_captions": True,
}


@pytest.fixture
def db():
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    name = "clipcut_test"
    client.drop_database(name)
    yield client[name]
    client.drop_database(name)


def _project(db, pid="p1"):
    db.projects.insert_one({
        "id": pid, "status": "ready", "video_path": "/tmp/source.mp4",
        "duration": 10.0, "width": 1080, "height": 1920,
        "words": [{"text": "hi", "start": 0.0, "end": 0.4, "type": "word"}],
        "cut_settings": {"pause_threshold": 0.8, "remove_fillers": True, "disabled": []},
        "reel_settings": dict(REEL), "caption_style": "bold",
        "export": {"status": "idle", "progress": 0, "error": None},
    })


def test_export_writes_done_state(db, monkeypatch, tmp_path):
    _project(db)
    out = tmp_path / "export.mp4"
    out.write_bytes(b"video-bytes")

    monkeypatch.setattr(eh, "project_dir", lambda pid: tmp_path)
    monkeypatch.setattr(
        eh.render_engine, "render_export",
        lambda **kw: {"width": 1080, "height": 1920, "duration": 9.0},
    )
    monkeypatch.setattr(eh.cloudinary_svc, "enabled", lambda: False)

    jobs_mod.enqueue(db, "p1", "export",
                     {"caption_style": "neon", "reel": dict(REEL)})
    worker_mod.run_once(db, "w1")

    exp = db.projects.find_one({"id": "p1"})["export"]
    assert exp["status"] == "done"
    assert exp["progress"] == 100
    assert exp["size"] == len(b"video-bytes")


def test_export_failure_records_error_not_stuck_processing(db, monkeypatch, tmp_path):
    _project(db, "p2")
    monkeypatch.setattr(eh, "project_dir", lambda pid: tmp_path)

    def boom(**kw):
        raise RuntimeError("ffmpeg exited 1")

    monkeypatch.setattr(eh.render_engine, "render_export", boom)
    jobs_mod.enqueue(db, "p2", "export",
                     {"caption_style": "bold", "reel": dict(REEL)})
    worker_mod.run_once(db, "w1")

    exp = db.projects.find_one({"id": "p2"})["export"]
    assert exp["status"] == "error"
    assert "ffmpeg" in exp["error"]


def test_export_honours_cancellation_before_render(db, monkeypatch, tmp_path):
    _project(db, "p3")
    monkeypatch.setattr(eh, "project_dir", lambda pid: tmp_path)
    called = {"n": 0}

    def counting(**kw):
        called["n"] += 1
        return {}

    monkeypatch.setattr(eh.render_engine, "render_export", counting)
    jid = jobs_mod.enqueue(db, "p3", "export",
                           {"caption_style": "bold", "reel": dict(REEL)})
    jobs_mod.request_cancel(db, jid)
    worker_mod.run_once(db, "w1")

    assert called["n"] == 0
    assert db.jobs.find_one({"id": jid})["status"] == "cancelled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handlers.export'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/handlers/export.py`:

```python
"""Export job: cut, caption, master, optionally push to Cloudinary."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

import cloudinary_svc
import render_engine
import worker
from worker import Cancelled

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def project_dir(pid: str) -> Path:
    return DATA_DIR / pid


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(ctx) -> dict:
    from server import compute_cut_state  # imported lazily; server owns cut math

    doc = ctx.db.projects.find_one({"id": ctx.project_id})
    if not doc:
        raise RuntimeError(f"project {ctx.project_id} not found")

    style_key = ctx.payload["caption_style"]
    reel = ctx.payload["reel"]
    pdir = project_dir(ctx.project_id)
    out_path = pdir / "export.mp4"

    if ctx.cancelled():
        raise Cancelled()

    def cb(p):
        stage = "cutting" if p < 68 else ("captioning" if p < 90 else "mastering")
        ctx.progress(p, stage)
        ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
            "export.progress": p, "export.stage": stage,
        }})

    try:
        state = compute_cut_state({**doc, "reel_settings": reel})
        meta = render_engine.render_export(
            source=Path(doc["video_path"]),
            words=doc.get("words") or [],
            ranges=state["keep_ranges"],
            style_key=style_key,
            burn=reel["burn_captions"],
            work_dir=pdir / "work",
            out_path=out_path,
            aspect=reel["aspect"],
            cinematic=reel["cinematic"],
            karaoke=reel["karaoke"],
            zoom_intensity=reel["zoom_intensity"],
            punch_ins=reel.get("punch_ins", True),
            punch_sensitivity=reel.get("punch_sensitivity", 0.5),
            progress_cb=cb,
        )
    except Exception as e:
        ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
            "export": {"status": "error", "progress": 0,
                       "error": str(e)[:500], "stage": "failed"},
        }})
        raise

    cloud = {}
    if cloudinary_svc.enabled():
        try:
            cloud = cloudinary_svc.upload_reel(
                out_path, public_id=f"reel_{ctx.project_id}",
                reframe=reel["aspect"] == "9:16",
            )
        except Exception as e:
            cloud = {"error": str(e)[:300]}

    ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
        "export": {
            "status": "done", "progress": 100, "error": None, "stage": "done",
            "path": str(out_path), "meta": meta,
            "size": out_path.stat().st_size,
            "finished_at": _now_iso(),
        },
        "cloud": cloud,
    }})
    shutil.rmtree(pdir / "work", ignore_errors=True)
    return {"path": str(out_path)}


worker.HANDLERS["export"] = run
```

Modify `backend/worker.py:_register_handlers`:

```python
def _register_handlers() -> None:
    import handlers.transcribe  # noqa: F401
    import handlers.export      # noqa: F401
```

Modify `backend/server.py` — replace the body of `start_export` after validation:

```python
    projects.update_one({"id": pid}, {"$set": {
        "caption_style": body.caption_style,
        "reel_settings": reel,
        "export": {"status": "processing", "progress": 0, "error": None, "stage": "cutting"},
    }})
    jid = jobs.enqueue(db, pid, "export",
                       {"caption_style": body.caption_style, "reel": reel})
    return {"ok": True, "reel_settings": reel, "job_id": jid}
```

Delete `_run_export` entirely. Add two routes near the other `@api` routes:

```python
@api.get("/jobs/{jid}")
def get_job(jid: str):
    doc = db.jobs.find_one({"id": jid}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "job not found")
    return doc


@api.post("/jobs/{jid}/cancel")
def cancel_job(jid: str):
    if not db.jobs.find_one({"id": jid}, {"_id": 1}):
        raise HTTPException(404, "job not found")
    jobs.request_cancel(db, jid)
    return {"ok": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_export.py -v`
Expected: PASS, 3 passed

- [ ] **Step 5: Verify no threads remain**

Run: `cd backend && grep -n "threading" server.py`
Expected: no matches. Remove the now-unused `import threading`.

- [ ] **Step 6: Manual end-to-end check**

Start the worker in one terminal and the API in another, then upload a clip
through the UI and export it. Confirm the export completes and the file plays.

```bash
cd backend && ../.venv-local/Scripts/python.exe worker.py
```

- [ ] **Step 7: Commit**

```bash
git add backend/handlers/export.py backend/worker.py backend/server.py backend/tests/test_handler_export.py
git commit -m "feat: run export as a cancellable queued job"
```

---

### Task 5: EDL v2 model and validation

**Files:**
- Create: `backend/plan/__init__.py`
- Create: `backend/plan/model.py`
- Create: `backend/tests/test_plan_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `plan.model.new_plan(project_id, source_path) -> dict`; `plan.model.validate(plan: dict) -> list[str]` returning error strings (empty list means valid); `plan.model.overlay(kind, start_in_output, duration, **extra) -> dict` factory that assigns an `id`; `plan.model.PLAN_VERSION = 2`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_plan_model.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plan import model


def test_new_plan_has_v2_shape():
    p = model.new_plan("p1", "data/p1/source.mp4")
    assert p["version"] == 2
    assert p["project_id"] == "p1"
    assert p["sources"] == {"main": "data/p1/source.mp4"}
    assert p["ranges"] == []
    assert p["overlays"] == []
    assert p["audio_overlays"] == []
    assert p["reframe"]["aspect"] == "9:16"
    assert p["captions"]["karaoke"] is True


def test_overlay_factory_assigns_unique_ids():
    a = model.overlay("broll", 1.0, 2.0, query="laptop")
    b = model.overlay("broll", 3.0, 2.0, query="desk")
    assert a["id"] != b["id"]
    assert a["enabled"] is True
    assert a["locked"] is False
    assert a["query"] == "laptop"


def test_validate_accepts_minimal_valid_plan():
    p = model.new_plan("p1", "data/p1/source.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    assert model.validate(p) == []


def test_validate_rejects_empty_ranges():
    p = model.new_plan("p1", "s.mp4")
    assert any("ranges" in e for e in model.validate(p))


def test_validate_rejects_unknown_source():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "nope", "start": 0.0, "end": 1.0}]
    assert any("nope" in e for e in model.validate(p))


def test_validate_rejects_inverted_range():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 5.0, "end": 5.0}]
    assert any("start < end" in e for e in model.validate(p))


def test_validate_rejects_negative_overlay_duration():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    p["overlays"] = [model.overlay("broll", 0.5, -1.0)]
    assert any("duration" in e for e in model.validate(p))


def test_validate_rejects_bad_aspect():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    p["reframe"]["aspect"] = "4:3"
    assert any("aspect" in e for e in model.validate(p))


def test_validate_rejects_center_x_out_of_range():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    p["reframe"]["center_x"] = 1.7
    assert any("center_x" in e for e in model.validate(p))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_plan_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plan'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/plan/__init__.py`:

```python
"""EDL v2 plan model, assembly, and materialization."""
```

Create `backend/plan/model.py`:

```python
"""EDL v2: the reviewable edit plan.

Extends the video-use EDL v1 (SKILL.md) with reframe, captions, first-class
audio_overlays, and per-overlay enabled/locked/provenance so the plan can be
reviewed and partially regenerated.
"""

import uuid

PLAN_VERSION = 2
VALID_ASPECTS = ("9:16", "original")
VALID_OVERLAY_KINDS = ("broll", "graphic", "still")


def new_plan(project_id: str, source_path: str) -> dict:
    return {
        "version": PLAN_VERSION,
        "project_id": project_id,
        "sources": {"main": str(source_path)},
        "ranges": [],
        "reframe": {"aspect": "9:16", "center_x": 0.5},
        "captions": {"style": "bold", "karaoke": True, "burn": True},
        "overlays": [],
        "audio_overlays": [],
        "grade": "none",
        "total_duration_s": 0.0,
        "provider": None,
    }


def overlay(kind: str, start_in_output: float, duration: float, **extra) -> dict:
    item = {
        "id": f"ov_{uuid.uuid4().hex[:8]}",
        "kind": kind,
        "start_in_output": float(start_in_output),
        "duration": float(duration),
        "file": None,
        "enabled": True,
        "locked": False,
    }
    item.update(extra)
    return item


def validate(plan: dict) -> list:
    """Return a list of human-readable errors. Empty means valid."""
    errors = []

    if plan.get("version") != PLAN_VERSION:
        errors.append(f"version must be {PLAN_VERSION}, got {plan.get('version')!r}")

    sources = plan.get("sources")
    if not isinstance(sources, dict) or not sources:
        errors.append("sources must be a non-empty object")
        return errors

    ranges = plan.get("ranges")
    if not isinstance(ranges, list) or not ranges:
        errors.append("ranges must be a non-empty array")
        return errors

    for i, r in enumerate(ranges):
        if not isinstance(r, dict):
            errors.append(f"ranges[{i}] must be an object")
            continue
        if r.get("source") not in sources:
            errors.append(f"ranges[{i}].source {r.get('source')!r} is not in sources")
            continue
        try:
            start = float(r["start"])
            end = float(r["end"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"ranges[{i}] needs numeric start and end")
            continue
        if start < 0 or end <= start:
            errors.append(f"ranges[{i}] requires 0 <= start < end (got {start}, {end})")

    reframe = plan.get("reframe") or {}
    if reframe.get("aspect") not in VALID_ASPECTS:
        errors.append(f"reframe.aspect must be one of {VALID_ASPECTS}")
    cx = reframe.get("center_x", 0.5)
    try:
        if not 0.0 <= float(cx) <= 1.0:
            errors.append(f"reframe.center_x must be in [0, 1], got {cx}")
    except (TypeError, ValueError):
        errors.append(f"reframe.center_x must be numeric, got {cx!r}")

    for i, ov in enumerate(plan.get("overlays") or []):
        if ov.get("kind") not in VALID_OVERLAY_KINDS:
            errors.append(f"overlays[{i}].kind must be one of {VALID_OVERLAY_KINDS}")
        try:
            if float(ov["duration"]) <= 0:
                errors.append(f"overlays[{i}].duration must be > 0")
        except (KeyError, TypeError, ValueError):
            errors.append(f"overlays[{i}] needs a numeric duration")
        try:
            if float(ov["start_in_output"]) < 0:
                errors.append(f"overlays[{i}].start_in_output must be >= 0")
        except (KeyError, TypeError, ValueError):
            errors.append(f"overlays[{i}] needs a numeric start_in_output")

    return errors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_plan_model.py -v`
Expected: PASS, 9 passed

- [ ] **Step 5: Commit**

```bash
git add backend/plan backend/tests/test_plan_model.py
git commit -m "feat: add EDL v2 plan model with validation"
```

---

### Task 6: Materialize plans to disk for helpers/

**Files:**
- Create: `backend/plan/materialize.py`
- Create: `backend/tests/test_materialize.py`

**Interfaces:**
- Consumes: `plan.model` from Task 5.
- Produces: `plan.materialize.edit_dir(project_dir: Path) -> Path`; `plan.materialize.write(plan: dict, project_dir: Path) -> Path` writing `edit/edl.json` with source paths made absolute; `plan.materialize.clean(project_dir: Path) -> None`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_materialize.py`:

```python
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plan import materialize, model


def test_write_creates_edl_json(tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    p = model.new_plan("p1", str(src))
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]

    out = materialize.write(p, tmp_path)

    assert out == tmp_path / "edit" / "edl.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert Path(data["sources"]["main"]).is_absolute()


def test_write_is_idempotent(tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    p = model.new_plan("p1", str(src))
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 1.0}]
    first = materialize.write(p, tmp_path).read_text(encoding="utf-8")
    second = materialize.write(p, tmp_path).read_text(encoding="utf-8")
    assert first == second


def test_clean_removes_edit_dir_but_not_source(tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    p = model.new_plan("p1", str(src))
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 1.0}]
    materialize.write(p, tmp_path)
    assert (tmp_path / "edit").exists()

    materialize.clean(tmp_path)

    assert not (tmp_path / "edit").exists()
    assert src.exists()


def test_clean_is_safe_when_nothing_exists(tmp_path):
    materialize.clean(tmp_path)  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_materialize.py -v`
Expected: FAIL — `ImportError: cannot import name 'materialize'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/plan/materialize.py`:

```python
"""Project a Mongo-held plan onto disk so helpers/ can consume it.

Mongo is the source of truth. Everything under <project_dir>/edit/ is derived
and safe to delete at any time.
"""

import json
import shutil
from pathlib import Path


def edit_dir(project_dir: Path) -> Path:
    return Path(project_dir) / "edit"


def write(plan: dict, project_dir: Path) -> Path:
    """Write edit/edl.json with absolute source paths. Returns the file path."""
    d = edit_dir(project_dir)
    d.mkdir(parents=True, exist_ok=True)
    out = dict(plan)
    out["sources"] = {
        name: str(Path(p).resolve())
        for name, p in (plan.get("sources") or {}).items()
    }
    path = d / "edl.json"
    path.write_text(
        json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def clean(project_dir: Path) -> None:
    shutil.rmtree(edit_dir(project_dir), ignore_errors=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_materialize.py -v`
Expected: PASS, 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/plan/materialize.py backend/tests/test_materialize.py
git commit -m "feat: materialize plans to edit/edl.json for helpers"
```

---

### Task 7: Face-centered crop in the helpers renderer

**Files:**
- Modify: `helpers/render.py:167-200` (`extract_segment` signature and cover branch)
- Create: `tests/test_render_cover.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `helpers.render.extract_segment(..., cover: bool = False, center_x: float = 0.5)` and a new pure helper `helpers.render.cover_crop_filter(src_w, src_h, center_x, draft=False) -> str` returning the ffmpeg `scale=...,crop=...` chain. The pure helper is what tests target; `extract_segment` calls it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_render_cover.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))

import render


def test_centered_crop_matches_legacy_behaviour():
    f = render.cover_crop_filter(1920, 1080, center_x=0.5)
    assert "crop=1080:1920" in f
    # A centred subject on a 1920x1080 source: crop x offset lands mid-frame.
    assert ":x=" in f


def test_off_centre_subject_shifts_crop_left():
    left = render.cover_crop_filter(1920, 1080, center_x=0.2)
    centre = render.cover_crop_filter(1920, 1080, center_x=0.5)
    x_left = int(left.split(":x=")[1].split(":")[0])
    x_centre = int(centre.split(":x=")[1].split(":")[0])
    assert x_left < x_centre


def test_crop_never_leaves_the_frame():
    for cx in (0.0, 0.01, 0.99, 1.0):
        f = render.cover_crop_filter(1920, 1080, center_x=cx)
        x = int(f.split(":x=")[1].split(":")[0])
        assert x >= 0
        assert x + 1080 <= 1920


def test_draft_uses_smaller_canvas():
    assert "720:1280" in render.cover_crop_filter(1920, 1080, 0.5, draft=True)


def test_already_vertical_source_is_not_cropped_horizontally():
    f = render.cover_crop_filter(1080, 1920, center_x=0.5)
    x = int(f.split(":x=")[1].split(":")[0])
    assert x == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../.venv-local/Scripts/python.exe -m pytest tests/test_render_cover.py -v`
Expected: FAIL — `AttributeError: module 'render' has no attribute 'cover_crop_filter'`

- [ ] **Step 3: Write minimal implementation**

Add to `helpers/render.py`, above `extract_segment`:

```python
def cover_crop_filter(src_w: int, src_h: int, center_x: float = 0.5,
                      draft: bool = False) -> str:
    """Scale-to-cover then crop to a vertical canvas, keeping center_x in frame.

    center_x is the subject's horizontal position as a fraction of source width.
    The crop window is clamped so it never runs past either edge.
    """
    out_w, out_h = (720, 1280) if draft else (1080, 1920)
    if not src_w or not src_h:
        return f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h}:x=0:y=0"

    # Scale so both dimensions cover the canvas.
    scale_f = max(out_w / src_w, out_h / src_h)
    scaled_w = int(round(src_w * scale_f))
    scaled_h = int(round(src_h * scale_f))
    scaled_w += scaled_w % 2
    scaled_h += scaled_h % 2

    x = int(round(center_x * scaled_w - out_w / 2))
    x = max(0, min(x, max(0, scaled_w - out_w)))
    y = max(0, (scaled_h - out_h) // 4)  # bias upward; heads sit high in frame

    return f"scale={scaled_w}:{scaled_h},crop={out_w}:{out_h}:x={x}:y={y}"
```

Change the `extract_segment` signature to add `center_x: float = 0.5` after
`cover: bool = False`, and replace the cover branch:

```python
    portrait = is_portrait_source(source)
    if cover:
        size = probe_video_size(source) or (1920, 1080)
        scale = cover_crop_filter(size[0], size[1], center_x, draft=draft)
    elif draft:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `../.venv-local/Scripts/python.exe -m pytest tests/test_render_cover.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Run the existing helpers suite for regressions**

Run: `../.venv-local/Scripts/python.exe -m pytest tests/ -v`
Expected: PASS. `cover` defaults to centred, so prior behaviour is unchanged.

- [ ] **Step 6: Commit**

```bash
git add helpers/render.py tests/test_render_cover.py
git commit -m "feat: face-centered cover crop in helpers renderer"
```

---

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

### Task 9: Bundle the caption font

**Files:**
- Create: `assets/fonts/README.md`
- Create: `assets/fonts/ClipCutSans-Bold.ttf` (downloaded, see Step 1)
- Modify: `helpers/captions_ass.py` (font name constant)
- Modify: `helpers/edl.py:96` (`default_subtitle_font`)
- Create: `tests/test_font_bundle.py`

**Interfaces:**
- Consumes: `helpers.captions_ass.CAPTION_STYLES` from Task 8.
- Produces: `helpers.captions_ass.FONTS_DIR: Path` pointing at `assets/fonts/`, and `helpers.captions_ass.font_available() -> bool`.

Rationale: `FONT = "Liberation Sans"` is not present on a default Windows install,
and libass substitutes silently rather than erroring — captions render in an
unintended typeface with no warning.

- [ ] **Step 1: Add the font file**

Download DejaVu Sans Bold (unrestricted licence, ships with a permissive
"do anything" grant) and place it at `assets/fonts/ClipCutSans-Bold.ttf`:

```bash
mkdir -p assets/fonts
curl -L -o assets/fonts/ClipCutSans-Bold.ttf \
  https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf
```

Create `assets/fonts/README.md`:

```markdown
# Bundled fonts

`ClipCutSans-Bold.ttf` is DejaVu Sans Bold, renamed for a stable internal family
name. DejaVu is released under a permissive licence allowing redistribution and
modification; see https://dejavu-fonts.github.io/License.html.

It is bundled because libass silently substitutes a missing font rather than
failing, which would ship captions in the wrong typeface with no error.
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_font_bundle.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))

import captions_ass


def test_fonts_dir_exists():
    assert captions_ass.FONTS_DIR.is_dir()


def test_bundled_font_file_present():
    assert captions_ass.font_available() is True


def test_every_style_uses_the_bundled_family():
    for name, style in captions_ass.CAPTION_STYLES.items():
        assert style["font"] == captions_ass.FONT, name
```

- [ ] **Step 3: Run test to verify it fails**

Run: `../.venv-local/Scripts/python.exe -m pytest tests/test_font_bundle.py -v`
Expected: FAIL — `AttributeError: module 'captions_ass' has no attribute 'FONTS_DIR'`

- [ ] **Step 4: Write minimal implementation**

Add near the top of `helpers/captions_ass.py`:

```python
FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
FONT_FILE = FONTS_DIR / "ClipCutSans-Bold.ttf"
FONT = "DejaVu Sans"           # the family name inside the bundled TTF
FONT_FALLBACK = "Liberation Sans"


def font_available() -> bool:
    return FONT_FILE.is_file()
```

Ensure every entry in `CAPTION_STYLES` uses `"font": FONT`.

Modify `helpers/edl.py:96`:

```python
def default_subtitle_font() -> str:
    try:
        import captions_ass
        return captions_ass.FONT
    except Exception:
        return "Liberation Sans"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `../.venv-local/Scripts/python.exe -m pytest tests/test_font_bundle.py -v`
Expected: PASS, 3 passed

- [ ] **Step 6: Verify ffmpeg actually resolves the bundled font**

```bash
../.venv-local/Scripts/python.exe -c "
import sys, pathlib
sys.path.insert(0, 'helpers')
import captions_ass as c
print('font file:', c.FONT_FILE, c.font_available())
"
```
Expected: prints the path and `True`.

- [ ] **Step 7: Commit**

```bash
git add assets/fonts helpers/captions_ass.py helpers/edl.py tests/test_font_bundle.py
git commit -m "feat: bundle caption font instead of relying on system Liberation Sans"
```

---

### Task 10: Build an EDL v2 from current project state

**Files:**
- Create: `backend/plan/assemble.py`
- Create: `backend/tests/test_assemble.py`

**Interfaces:**
- Consumes: `plan.model` (Task 5); `cuts.compute_spans` and the existing `compute_cut_state` shape.
- Produces: `plan.assemble.from_project(doc: dict, cut_state: dict) -> dict` returning a validated EDL v2 whose `ranges` mirror `cut_state["keep_ranges"]`, whose `reframe`/`captions` come from the project's `reel_settings` and `caption_style`, and whose `overlays` are empty (populated in plan 2).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_assemble.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plan import assemble, model

DOC = {
    "id": "p1",
    "video_path": "/data/p1/source.mp4",
    "caption_style": "neon",
    "reel_settings": {
        "aspect": "9:16", "cinematic": True, "karaoke": True,
        "zoom_intensity": 1.0, "punch_ins": True, "punch_sensitivity": 0.5,
        "burn_captions": True,
    },
}
CUT_STATE = {
    "keep_ranges": [(0.0, 2.4), (3.1, 6.0)],
    "kept_duration": 5.3,
}


def test_ranges_mirror_keep_ranges():
    p = assemble.from_project(DOC, CUT_STATE)
    assert len(p["ranges"]) == 2
    assert p["ranges"][0]["start"] == 0.0
    assert p["ranges"][0]["end"] == 2.4
    assert p["ranges"][1]["source"] == "main"


def test_captions_come_from_project_settings():
    p = assemble.from_project(DOC, CUT_STATE)
    assert p["captions"]["style"] == "neon"
    assert p["captions"]["karaoke"] is True
    assert p["captions"]["burn"] is True


def test_reframe_aspect_comes_from_reel_settings():
    p = assemble.from_project(DOC, CUT_STATE)
    assert p["reframe"]["aspect"] == "9:16"


def test_original_aspect_is_preserved():
    doc = {**DOC, "reel_settings": {**DOC["reel_settings"], "aspect": "original"}}
    assert assemble.from_project(doc, CUT_STATE)["reframe"]["aspect"] == "original"


def test_total_duration_is_the_sum_of_ranges():
    p = assemble.from_project(DOC, CUT_STATE)
    assert abs(p["total_duration_s"] - 5.3) < 0.01


def test_output_validates():
    assert model.validate(assemble.from_project(DOC, CUT_STATE)) == []


def test_center_x_defaults_to_half_when_absent():
    assert assemble.from_project(DOC, CUT_STATE)["reframe"]["center_x"] == 0.5


def test_center_x_is_carried_through_when_present():
    doc = {**DOC, "subject_center_x": 0.27}
    assert assemble.from_project(doc, CUT_STATE)["reframe"]["center_x"] == 0.27
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_assemble.py -v`
Expected: FAIL — `ImportError: cannot import name 'assemble'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/plan/assemble.py`:

```python
"""Turn a project document plus computed cuts into an EDL v2."""

from plan import model


def from_project(doc: dict, cut_state: dict) -> dict:
    plan = model.new_plan(doc["id"], doc.get("video_path") or "")
    reel = doc.get("reel_settings") or {}

    ranges = []
    total = 0.0
    for start, end in cut_state.get("keep_ranges") or []:
        start = float(start)
        end = float(end)
        ranges.append({"source": "main", "start": start, "end": end, "zoom": 1.0})
        total += end - start
    plan["ranges"] = ranges
    plan["total_duration_s"] = round(total, 3)

    plan["reframe"] = {
        "aspect": reel.get("aspect", "9:16"),
        "center_x": float(doc.get("subject_center_x", 0.5)),
    }
    plan["captions"] = {
        "style": doc.get("caption_style", "bold"),
        "karaoke": bool(reel.get("karaoke", True)),
        "burn": bool(reel.get("burn_captions", True)),
    }
    return plan
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_assemble.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/plan/assemble.py backend/tests/test_assemble.py
git commit -m "feat: assemble EDL v2 from project state and cuts"
```

---

### Task 11: Render an EDL v2 through the helpers pipeline

**Files:**
- Create: `backend/plan/render_plan.py`
- Create: `backend/tests/test_render_plan.py`

**Interfaces:**
- Consumes: `plan.model.validate` (Task 5), `plan.materialize.write` (Task 6), `helpers.render.extract_segment`/`concat_segments`/`build_final_composite`/`apply_loudnorm_two_pass`, `helpers.captions_ass.build_ass` (Task 8).
- Produces: `plan.render_plan.render(plan: dict, project_dir: Path, out_path: Path, words: list, progress_cb=None, cancel_cb=None) -> dict` returning `{"width", "height", "duration"}`. Raises `ValueError` listing plan errors when validation fails. Calls `cancel_cb()` before each ffmpeg spawn and raises `worker.Cancelled` when it returns True.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_render_plan.py`:

```python
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

from plan import model, render_plan


def _plan(tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    p = model.new_plan("p1", str(src))
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0, "zoom": 1.0}]
    p["total_duration_s"] = 2.0
    return p


def test_invalid_plan_raises_with_reasons(tmp_path):
    p = _plan(tmp_path)
    p["ranges"] = []
    with pytest.raises(ValueError) as exc:
        render_plan.render(p, tmp_path, tmp_path / "out.mp4", words=[])
    assert "ranges" in str(exc.value)


def test_progress_callback_reports_named_stages(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(render_plan, "_extract_all", lambda *a, **k: [tmp_path / "s0.mp4"])
    monkeypatch.setattr(render_plan, "_concat", lambda *a, **k: tmp_path / "base.mp4")
    monkeypatch.setattr(render_plan, "_composite", lambda *a, **k: tmp_path / "comp.mp4")
    monkeypatch.setattr(render_plan, "_master", lambda *a, **k: None)
    monkeypatch.setattr(render_plan, "_probe_out",
                        lambda p: {"width": 1080, "height": 1920, "duration": 2.0})

    render_plan.render(_plan(tmp_path), tmp_path, tmp_path / "out.mp4",
                       words=[], progress_cb=lambda p, s: seen.append(s))

    assert [s for s in ("cutting", "compositing", "captioning", "mastering")
            if s in seen] == ["cutting", "compositing", "captioning", "mastering"]


def test_cancel_before_extract_raises(tmp_path, monkeypatch):
    import worker

    monkeypatch.setattr(render_plan, "_extract_all",
                        lambda *a, **k: pytest.fail("must not extract"))
    with pytest.raises(worker.Cancelled):
        render_plan.render(_plan(tmp_path), tmp_path, tmp_path / "out.mp4",
                           words=[], cancel_cb=lambda: True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_render_plan.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_plan'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/plan/render_plan.py`:

```python
"""Render an EDL v2 using the helpers/ pipeline.

Stage order is fixed: cut -> concat -> composite overlays -> burn captions ->
two-pass loudnorm. Captions are always last before mastering so overlays cannot
cover them.
"""

import json
import subprocess
from pathlib import Path

import captions_ass
import render as helpers_render
import worker
from plan import materialize, model


def _probe_out(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    stream = (data.get("streams") or [{}])[0]
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration": float((data.get("format") or {}).get("duration") or 0.0),
    }


def _extract_all(plan, project_dir, work_dir, cover, center_x):
    sources = plan["sources"]
    paths = []
    for i, r in enumerate(plan["ranges"]):
        seg = work_dir / f"seg_{i:04d}.mp4"
        helpers_render.extract_segment(
            Path(sources[r["source"]]),
            float(r["start"]),
            float(r["end"]) - float(r["start"]),
            helpers_render.resolve_grade_filter(plan.get("grade")),
            seg,
            zoom=float(r.get("zoom") or 1.0),
            cover=cover,
            center_x=center_x,
        )
        paths.append(seg)
    return paths


def _concat(paths, work_dir, edit_dir):
    base = work_dir / "base.mp4"
    helpers_render.concat_segments(paths, base, edit_dir)
    return base


def _composite(base, plan, subs_path, work_dir, edit_dir):
    out = work_dir / "composite.mp4"
    overlays = [o for o in (plan.get("overlays") or []) if o.get("enabled")]
    helpers_render.build_final_composite(base, overlays, subs_path, out, edit_dir)
    return out


def _master(src, out_path):
    helpers_render.apply_loudnorm_two_pass(src, out_path)


def render(plan: dict, project_dir: Path, out_path: Path, words: list,
           progress_cb=None, cancel_cb=None) -> dict:
    errors = model.validate(plan)
    if errors:
        raise ValueError("invalid plan: " + "; ".join(errors))

    def tick(p, stage):
        if progress_cb:
            progress_cb(p, stage)

    def check_cancel():
        if cancel_cb and cancel_cb():
            raise worker.Cancelled()

    project_dir = Path(project_dir)
    edit_dir = materialize.edit_dir(project_dir)
    work_dir = edit_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    materialize.write(plan, project_dir)

    aspect = (plan.get("reframe") or {}).get("aspect", "9:16")
    cover = aspect == "9:16"
    center_x = float((plan.get("reframe") or {}).get("center_x", 0.5))

    check_cancel()
    tick(10, "cutting")
    segments = _extract_all(plan, project_dir, work_dir, cover, center_x)

    check_cancel()
    tick(55, "compositing")
    base = _concat(segments, work_dir, edit_dir)

    subs_path = None
    caps = plan.get("captions") or {}
    if caps.get("burn") and words:
        check_cancel()
        tick(70, "captioning")
        probe = _probe_out(base)
        subs_path = edit_dir / "captions.ass"
        style = captions_ass.CAPTION_STYLES.get(
            caps.get("style", "bold"), captions_ass.CAPTION_STYLES["bold"]
        )
        captions_ass.build_ass(
            words,
            [{"start": r["start"], "end": r["end"]} for r in plan["ranges"]],
            subs_path, style, probe["width"], probe["height"],
            karaoke=bool(caps.get("karaoke", True)),
            fonts_dir=captions_ass.FONTS_DIR,
        )
    else:
        tick(70, "captioning")

    composited = _composite(base, plan, subs_path, work_dir, edit_dir)

    check_cancel()
    tick(90, "mastering")
    _master(composited, out_path)

    tick(100, "done")
    return _probe_out(out_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_render_plan.py -v`
Expected: PASS, 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/plan/render_plan.py backend/tests/test_render_plan.py
git commit -m "feat: render EDL v2 through the helpers pipeline"
```

---

### Task 12: Parity test, then delete the old renderer

**Files:**
- Create: `backend/tests/test_render_parity.py`
- Create: `backend/tests/fixtures/README.md`
- Modify: `backend/handlers/export.py` (switch to `plan.render_plan.render`)
- Delete: `backend/render_engine.py`
- Modify: `backend/server.py` (drop `import render_engine`; `list_styles` reads `captions_ass.CAPTION_STYLES`)

**Interfaces:**
- Consumes: everything from Tasks 5-11.
- Produces: `handlers.export.run` rendering via `plan.render_plan.render`. The project's `export` sub-document keeps exactly the same shape, so the frontend is untouched.

**This is the only task that deletes working code. The parity test gates it.**

- [ ] **Step 1: Create the fixture**

Generate a 6-second test clip with tone and a moving box, so cuts and crops are
visually checkable:

```bash
mkdir -p backend/tests/fixtures
ffmpeg -y -f lavfi -i "testsrc=size=1920x1080:rate=30:duration=6" \
       -f lavfi -i "sine=frequency=440:duration=6" \
       -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest \
       backend/tests/fixtures/parity_src.mp4
```

Create `backend/tests/fixtures/README.md`:

```markdown
# Test fixtures

`parity_src.mp4` — 6s 1920x1080 30fps synthetic clip with a 440Hz tone,
generated by ffmpeg lavfi. Regenerate with the command in Task 12 of
docs/superpowers/plans/2026-08-17-clipcut-foundation.md.
```

- [ ] **Step 2: Write the parity test**

Create `backend/tests/test_render_parity.py`:

```python
"""Gate for deleting render_engine.py: both renderers must agree."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

from plan import assemble, render_plan

FIXTURE = Path(__file__).parent / "fixtures" / "parity_src.mp4"

WORDS = [
    {"text": "one", "start": 0.5, "end": 0.9, "type": "word"},
    {"text": "two", "start": 1.0, "end": 1.4, "type": "word"},
    {"text": "three", "start": 3.0, "end": 3.5, "type": "word"},
]
CUT_STATE = {"keep_ranges": [(0.0, 2.0), (2.8, 4.5)], "kept_duration": 3.7}


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture not generated")
def test_both_renderers_agree(tmp_path):
    """The actual gate: old and new renderer must produce the same geometry
    and duration from identical input. This is what licenses the deletion."""
    import render_engine  # still present at this point in the plan

    doc = {
        "id": "parity",
        "video_path": str(FIXTURE),
        "caption_style": "bold",
        "reel_settings": {
            "aspect": "9:16", "cinematic": False, "karaoke": True,
            "zoom_intensity": 1.0, "punch_ins": False, "punch_sensitivity": 0.5,
            "burn_captions": True,
        },
    }

    old_out = tmp_path / "old.mp4"
    old_meta = render_engine.render_export(
        source=FIXTURE,
        words=WORDS,
        ranges=CUT_STATE["keep_ranges"],
        style_key="bold",
        burn=True,
        work_dir=tmp_path / "work_old",
        out_path=old_out,
        aspect="9:16",
        cinematic=False,
        karaoke=True,
        zoom_intensity=1.0,
        punch_ins=False,
        punch_sensitivity=0.5,
        progress_cb=lambda p: None,
    )

    new_out = tmp_path / "new.mp4"
    plan = assemble.from_project(doc, CUT_STATE)
    new_meta = render_plan.render(plan, tmp_path, new_out, words=WORDS)

    assert old_out.is_file() and new_out.is_file()
    assert new_meta["width"] == old_meta["width"] == 1080
    assert new_meta["height"] == old_meta["height"] == 1920
    # Same cuts in, so durations must match within encoder/fade slop.
    assert abs(new_meta["duration"] - old_meta["duration"]) < 0.25


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture not generated")
def test_original_aspect_keeps_source_geometry(tmp_path):
    doc = {
        "id": "parity2",
        "video_path": str(FIXTURE),
        "caption_style": "bold",
        "reel_settings": {
            "aspect": "original", "cinematic": False, "karaoke": False,
            "zoom_intensity": 1.0, "punch_ins": False, "punch_sensitivity": 0.5,
            "burn_captions": False,
        },
    }
    plan = assemble.from_project(doc, CUT_STATE)
    out = tmp_path / "out2.mp4"

    meta = render_plan.render(plan, tmp_path, out, words=[])

    assert meta["width"] > meta["height"]
```

- [ ] **Step 3: Run the parity test**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_render_parity.py -v`
Expected: PASS, 2 passed. **Do not proceed to Step 4 until this passes.**

- [ ] **Step 4: Switch the export handler to the new renderer**

In `backend/handlers/export.py`, replace the `render_engine.render_export(...)`
call with:

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

and change the imports at the top:

```python
import cloudinary_svc
import worker
from plan import assemble, render_plan
from worker import Cancelled
```

Change the progress callback so it takes both arguments:

```python
    def cb(p, stage):
        ctx.progress(p, stage)
        ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
            "export.progress": p, "export.stage": stage,
        }})
```

- [ ] **Step 5: Delete the old renderer**

```bash
git rm backend/render_engine.py
```

In `backend/server.py`: remove `import render_engine`, and change `list_styles`
and the style validation in `start_export` to use `captions_ass`:

```python
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).parent.parent / "helpers"))
import captions_ass
```

```python
@api.get("/styles")
def list_styles():
    return {"styles": list(captions_ass.CAPTION_STYLES.keys())}
```

`complete_upload` also calls `render_engine.probe` and
`render_engine.make_thumbnail`. Move both into a new
`backend/probe.py` (copy `probe`, `make_thumbnail`, `_even`, and `_run` verbatim
from the deleted file) and import that instead.

- [ ] **Step 6: Run the whole suite**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/ -v`
Then: `../.venv-local/Scripts/python.exe -m pytest tests/ -v` (from repo root)
Expected: PASS in both.

- [ ] **Step 7: Manual end-to-end check**

Start worker + API + frontend, upload the same clip used earlier, export with
9:16 + neon + karaoke, and confirm the output is 1080x1920 with burned captions
and the speaker in frame. Extract a frame and look at it:

```bash
ffmpeg -y -ss 2 -i backend/data/<pid>/export.mp4 -frames:v 1 /tmp/check.png
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: single renderer, delete render_engine.py

Parity test asserts the helpers pipeline produces the same geometry and
duration as the old backend renderer before removing it."
```

---

### Task 13: Dev script for the three processes

**Files:**
- Create: `scripts/dev.ps1`
- Modify: `README.md` (add a ClipCut section)

**Interfaces:**
- Consumes: nothing.
- Produces: a single command that starts API, worker, and frontend.

- [ ] **Step 1: Write the script**

Create `scripts/dev.ps1`:

```powershell
# Start ClipCut: API, worker, and frontend.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv-local\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "venv missing at $py" }

$svc = Get-Service -Name MongoDB -ErrorAction SilentlyContinue
if ($null -eq $svc -or $svc.Status -ne "Running") {
    throw "MongoDB service is not running. Start it, then retry."
}

Start-Process -FilePath $py -ArgumentList "-m","uvicorn","server:app","--port","8000" `
              -WorkingDirectory (Join-Path $root "backend")
Start-Process -FilePath $py -ArgumentList "worker.py" `
              -WorkingDirectory (Join-Path $root "backend")
Start-Process -FilePath "npm" -ArgumentList "start" `
              -WorkingDirectory (Join-Path $root "frontend")

Write-Output "API      http://localhost:8000"
Write-Output "Frontend http://localhost:3000"
Write-Output "Worker   running (check its window for job logs)"
```

- [ ] **Step 2: Run it**

Run: `powershell -ExecutionPolicy Bypass -File scripts\dev.ps1`
Expected: three windows open; `curl http://localhost:8000/api/` returns
`{"status":"ok"}`.

- [ ] **Step 3: Document it**

Add to `README.md` after the "Local app (Windows)" section:

```markdown
### ClipCut (web reel editor)

ClipCut is the browser-based reel editor in `backend/` + `frontend/`. It needs
MongoDB running locally and an ElevenLabs key in `backend/.env`.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

This starts three processes: the API on :8000, the job worker, and the frontend
on :3000. The worker runs all transcription and rendering; the API only enqueues.

Backend dependencies are the eight packages the code actually imports — do not
install `backend/requirements.txt`, which is a stale Emergent lockfile pinning a
private wheel.
```

- [ ] **Step 4: Commit**

```bash
git add scripts/dev.ps1 README.md
git commit -m "docs: add ClipCut dev script and README section"
```

---

## Self-Review

**Spec coverage.** Every spec section for steps 1-3 maps to a task: job queue
(Tasks 1-2), transcription and export migration (3-4), EDL v2 (5), materialize
(6), renderer convergence (7-9, 11-12), assembly (10), operations (13). The
`GET /api/jobs/{jid}` and cancel routes land in Task 4. Deferred to plan 2, as
the spec states: decision providers, `POST /api/projects/{pid}/plan`, the
overlay PATCH route, plan-review UI, and b-roll.

**Known gaps carried into plan 2.** `plan.assemble.from_project` always emits
empty `overlays`, and `render_plan._composite` filters on `enabled` but has
nothing to filter yet. Both are intentional: the schema and the code path exist
so plan 2 adds data, not structure.

**Type consistency.** `Ctx.progress(p, stage)` is two-positional throughout;
Task 4's initial `cb(p)` is explicitly widened to `cb(p, stage)` in Task 12
Step 4 when the renderer changes. `model.validate` returns `list[str]`
everywhere. `cover_crop_filter` is used only by `extract_segment`. `CAPTION_STYLES`
moves from `render_engine` to `captions_ass` in Task 8 and every later reference
uses the new home.

**Risk.** Task 12 is the only destructive task and is gated on Task 12 Step 3
passing. If parity fails, stop and reconcile rather than deleting.
