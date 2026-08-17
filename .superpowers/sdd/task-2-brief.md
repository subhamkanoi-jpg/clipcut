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

