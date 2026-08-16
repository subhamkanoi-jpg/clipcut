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
