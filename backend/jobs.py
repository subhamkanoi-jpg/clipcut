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
