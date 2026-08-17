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


def test_heartbeat_extends_lease(db):
    jid = jobs_mod.enqueue(db, "proj1", "render")
    jobs_mod.claim(db, ["render"], "worker-a")
    doc_before = db.jobs.find_one({"id": jid})
    lease_before = doc_before["lease_expires_at"]

    result = jobs_mod.heartbeat(db, jid)

    doc_after = db.jobs.find_one({"id": jid})
    lease_after = doc_after["lease_expires_at"]

    assert result is True
    assert lease_after > lease_before


def test_heartbeat_returns_false_when_not_processing(db):
    jid = jobs_mod.enqueue(db, "proj1", "render")
    result = jobs_mod.heartbeat(db, jid)
    assert result is False


def test_set_progress_updates_fields(db):
    jid = jobs_mod.enqueue(db, "proj1", "render")
    jobs_mod.set_progress(db, jid, 42, "cutting")
    doc = db.jobs.find_one({"id": jid})
    assert doc["progress"] == 42
    assert isinstance(doc["progress"], int)
    assert doc["stage"] == "cutting"


def test_cancel_sets_terminal_status(db):
    jid = jobs_mod.enqueue(db, "proj1", "render")
    jobs_mod.cancel(db, jid)
    doc = db.jobs.find_one({"id": jid})
    assert doc["status"] == "cancelled"
    assert doc["stage"] == "cancelled"
    assert doc["finished_at"] is not None


# Finding 5b: every path that fails or cancels a job OUTSIDE a handler's own
# try/except (no handler registered, reconcile_stale's exceeded-max-attempts
# branch, an exception raised before a handler's own try) used to leave the
# matching project document stranded showing "processing" -- exactly the
# failure mode the queue exists to prevent. fail()/cancel() now reconcile the
# project themselves so this is true structurally, for any caller, instead
# of being duplicated (and inevitably missed) per handler.


def test_fail_reconciles_export_project_out_of_processing(db):
    db.projects.insert_one({"id": "p1", "export": {
        "status": "processing", "progress": 40, "error": None, "stage": "cutting",
    }})
    jid = jobs_mod.enqueue(db, "p1", "export")
    jobs_mod.fail(db, jid, "ffmpeg exited 1")

    exp = db.projects.find_one({"id": "p1"})["export"]
    assert exp["status"] == "error"
    assert exp["error"] == "ffmpeg exited 1"
    assert exp["stage"] == "failed"
    assert exp["progress"] == 0


def test_fail_reconciles_transcribe_project_out_of_processing(db):
    db.projects.insert_one({"id": "p2", "status": "transcribing"})
    jid = jobs_mod.enqueue(db, "p2", "transcribe")
    jobs_mod.fail(db, jid, "Scribe returned 401")

    doc = db.projects.find_one({"id": "p2"})
    assert doc["status"] == "error"
    assert doc["error"] == "Scribe returned 401"


def test_fail_ignores_unknown_kind_project_shape(db):
    # A kind with no registered reconciler (e.g. a not-yet-deployed "plan"
    # kind) must not blow up -- there's no project schema to reconcile
    # against, so this is a safe no-op rather than an error.
    db.projects.insert_one({"id": "p3"})
    jid = jobs_mod.enqueue(db, "p3", "plan")
    jobs_mod.fail(db, jid, "boom")
    assert db.jobs.find_one({"id": jid})["status"] == "error"
    doc = db.projects.find_one({"id": "p3"}, {"_id": 0})
    assert doc == {"id": "p3"}


def test_cancel_reconciles_export_project_out_of_processing(db):
    db.projects.insert_one({"id": "p4", "export": {
        "status": "processing", "progress": 55, "error": None, "stage": "compositing",
    }})
    jid = jobs_mod.enqueue(db, "p4", "export")
    jobs_mod.cancel(db, jid)

    exp = db.projects.find_one({"id": "p4"})["export"]
    assert exp["status"] == "cancelled"
    assert exp["stage"] == "cancelled"
    assert exp["progress"] == 0
    assert exp["error"] is None


def test_reconcile_stale_exceeded_attempts_reconciles_project(db):
    # This is the second of the two gap paths named in Finding 5b: a job
    # that exhausts its retries in reconcile_stale (never touching a
    # handler's own try/except at all) must still pull its project out of
    # "processing".
    db.projects.insert_one({"id": "p5", "export": {
        "status": "processing", "progress": 20, "error": None, "stage": "cutting",
    }})
    jid = jobs_mod.enqueue(db, "p5", "export")
    db.jobs.update_one({"id": jid}, {"$set": {
        "status": "processing",
        "attempts": 3,
        "lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=5),
    }})

    jobs_mod.reconcile_stale(db)

    exp = db.projects.find_one({"id": "p5"})["export"]
    assert exp["status"] == "error"
    assert "attempts" in exp["error"]
