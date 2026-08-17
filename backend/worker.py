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
from errors import Cancelled

# Importing handler modules registers them in HANDLERS. Keep after HANDLERS exists.
def _register_handlers() -> None:
    import handlers.transcribe  # noqa: F401
    import handlers.export      # noqa: F401

POLL_S = 1.0

# reconcile_stale() used to run only at worker boot, so a job whose lease was
# still inside its 60s window when the worker died (e.g. killed a few seconds
# after a heartbeat, restarted inside the lease) would sit in "processing"
# forever -- neither requeued nor failed. Running it periodically from the
# poll loop closes that gap without a dependency or a background thread.
RECONCILE_INTERVAL_S = 30.0

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
        leased = jobs_mod.heartbeat(self.db, self.job["id"])
        if not leased:
            log.warning(
                "lost lease on job %s; another worker may have reclaimed it",
                self.job["id"],
            )

    def cancelled(self) -> bool:
        return jobs_mod.is_cancelled(self.db, self.job["id"])


# `Cancelled` above is imported (not defined here) from errors.py, but stays
# accessible as `worker.Cancelled` for backward compatibility -- it's the
# same class object as errors.Cancelled, not a copy, so `except
# worker.Cancelled` and `except errors.Cancelled` catch the same instances.
# New code should import from errors directly.

HANDLERS: dict = {}


def run_once(db, worker_id: str) -> bool:
    # Only claim kinds this worker actually has a handler for. Claiming any
    # queued kind (including ones no handler is registered for) meant a job
    # enqueued for a not-yet-deployed kind (e.g. a new "plan" kind added by a
    # future sub-project) would be claimed and immediately destroyed with
    # "no handler for kind" instead of waiting in the queue for a worker that
    # knows about it.
    job = jobs_mod.claim(db, list(HANDLERS), worker_id)
    if not job:
        return False
    handler = HANDLERS.get(job["kind"])
    if handler is None:
        # Defense in depth: claim() above should make this unreachable in
        # normal operation since it only claims registered kinds, but keep
        # the guard (and its project reconciliation, via jobs_mod.fail) in
        # case HANDLERS is ever mutated between claim and dispatch.
        jobs_mod.fail(db, job["id"], f"no handler for kind {job['kind']!r}")
        return True
    ctx = Ctx(db=db, job=job, project_id=job["project_id"], payload=job.get("payload") or {})
    try:
        result = handler(ctx)
        jobs_mod.finish(db, job["id"], result or {})
    except Cancelled:
        jobs_mod.cancel(db, job["id"])
        log.info("job %s cancelled", job["id"])
    except Exception as e:
        log.exception("job %s failed", job["id"])
        jobs_mod.fail(db, job["id"], str(e))
    return True


def maybe_reconcile(db, last_reconcile_at: float, now: float,
                    interval_s: float = RECONCILE_INTERVAL_S) -> float:
    """Run reconcile_stale() if `interval_s` has elapsed since the last run.

    Returns the (possibly updated) "last reconciled at" timestamp so the
    caller can thread it through the poll loop. Takes `now` as a parameter
    (rather than calling time.monotonic() itself) so it's a pure function of
    its inputs and testable without real elapsed time.
    """
    if now - last_reconcile_at < interval_s:
        return last_reconcile_at
    requeued = jobs_mod.reconcile_stale(db)
    if requeued:
        log.info("requeued %d stale job(s)", requeued)
    return now


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    log.info("shutdown requested")


def main() -> None:
    # Helper modules (render.py, grade.py, pack_transcripts.py, ...) print
    # progress lines containing Unicode characters like "→". On Windows the
    # worker's stdout defaults to cp1252, which can't encode them, crashing
    # the job. Reconfigure stdio before anything can print or a job can be
    # claimed.
    from stdio import configure_stdio
    configure_stdio()

    _register_handlers()
    client = MongoClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    worker_id = f"{socket.gethostname()}-{os.getpid()}"

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    requeued = jobs_mod.reconcile_stale(db)
    if requeued:
        log.info("requeued %d stale job(s)", requeued)
    log.info("worker %s ready, handlers: %s", worker_id, sorted(HANDLERS))

    last_reconcile_at = time.monotonic()
    while not _shutdown:
        try:
            if not run_once(db, worker_id):
                time.sleep(POLL_S)
            last_reconcile_at = maybe_reconcile(db, last_reconcile_at, time.monotonic())
        except Exception:
            log.exception("worker loop error")
            time.sleep(POLL_S)
    log.info("worker stopped")


if __name__ == "__main__":
    # Re-enter under the module's real name. Handler modules do `import worker`,
    # so running this file directly would otherwise create a second module object
    # whose HANDLERS dict is distinct from this one (Cancelled is unaffected --
    # it lives in errors.py, which both module objects import identically).
    from worker import main as _main
    _main()
