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
    # Claim any queued job, regardless of handler registration
    # Then check if we have a handler for it
    all_kinds = db.jobs.distinct("kind", {"status": "queued"})
    kinds_to_claim = list(HANDLERS.keys()) + [k for k in all_kinds if k not in HANDLERS]
    if not kinds_to_claim:
        kinds_to_claim = ["__none__"]
    job = jobs_mod.claim(db, kinds_to_claim, worker_id)
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
