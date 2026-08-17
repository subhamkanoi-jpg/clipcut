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


# Every terminal job kind carries a matching project sub-document that must
# not be left showing "processing"/an in-flight stage once the job itself is
# error/cancelled -- that's the exact failure mode this queue was built to
# prevent. Rather than have every call site that can fail or cancel a job
# (worker.run_once's "no handler" branch, its generic exception handler,
# reconcile_stale's "exceeded max attempts" branch, ...) remember to also
# reconcile the project, `fail()` and `cancel()` below do it themselves, so
# it is structurally true for every path instead of duplicated per caller.
def _reconcile_transcribe_project(db, project_id: str, status: str, error: str | None) -> None:
    db.projects.update_one({"id": project_id}, {"$set": {
        "status": status, "error": error,
    }})


def _reconcile_export_project(db, project_id: str, status: str, error: str | None) -> None:
    db.projects.update_one({"id": project_id}, {"$set": {
        "export": {
            "status": status, "progress": 0, "error": error,
            "stage": "cancelled" if status == "cancelled" else "failed",
        },
    }})


def _reconcile_plan_project(db, project_id: str, status: str, error: str | None) -> None:
    # A stranded plan job should not leave plan_status at "planning".
    db.projects.update_one({"id": project_id}, {"$set": {
        "plan_status": "error" if status == "error" else status,
    }})


# kind -> function(db, project_id, status, error). Add an entry here whenever
# a new job kind gets its own project sub-document shape.
PROJECT_RECONCILERS = {
    "transcribe": _reconcile_transcribe_project,
    "export": _reconcile_export_project,
    "plan": _reconcile_plan_project,
}


def _reconcile_project(db, job_doc: dict | None, status: str, error: str | None) -> None:
    if not job_doc:
        return
    reconciler = PROJECT_RECONCILERS.get(job_doc.get("kind"))
    if reconciler:
        reconciler(db, job_doc["project_id"], status, error)


def fail(db, job_id: str, error: str) -> None:
    doc = db.jobs.find_one_and_update(
        {"id": job_id},
        {"$set": {
            "status": "error",
            "stage": "failed",
            "error": str(error)[:2000],
            "finished_at": _now(),
        }},
    )
    _reconcile_project(db, doc, "error", str(error)[:500])


def cancel(db, job_id: str) -> None:
    doc = db.jobs.find_one_and_update(
        {"id": job_id},
        {"$set": {
            "status": "cancelled",
            "stage": "cancelled",
            "finished_at": _now(),
        }},
    )
    _reconcile_project(db, doc, "cancelled", None)


def request_cancel(db, job_id: str) -> None:
    db.jobs.update_one({"id": job_id}, {"$set": {"cancel_requested": True}})


def is_cancelled(db, job_id: str) -> bool:
    doc = db.jobs.find_one({"id": job_id}, {"cancel_requested": 1})
    return bool(doc and doc.get("cancel_requested"))


def reconcile_stale(db) -> int:
    """Requeue processing jobs whose lease expired. Fail those past max attempts.

    Called on worker boot, and periodically from the poll loop (see
    worker.maybe_reconcile) so a job whose lease was still valid when its
    worker died doesn't sit in "processing" until the next restart. Jobs
    failed here go through fail() above, which also reconciles the project
    document so it can never be left stranded showing "processing" either.
    Returns how many were requeued.
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
