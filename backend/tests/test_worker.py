import logging
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


def test_handler_raising_cancelled_marks_job_cancelled(db):
    def handler(ctx):
        raise worker_mod.Cancelled()

    worker_mod.HANDLERS["dummy"] = handler
    try:
        jid = jobs_mod.enqueue(db, "proj1", "dummy")
        worker_mod.run_once(db, "w1")
        doc = db.jobs.find_one({"id": jid})
        assert doc["status"] == "cancelled"
        assert doc["stage"] == "cancelled"
        assert doc["finished_at"] is not None
    finally:
        del worker_mod.HANDLERS["dummy"]


def test_progress_warns_when_lease_lost(db, caplog):
    jid = jobs_mod.enqueue(db, "proj1", "render")
    job = jobs_mod.claim(db, ["render"], "worker-a")
    # Simulate another worker reclaiming/finishing the job so our lease is lost.
    db.jobs.update_one({"id": jid}, {"$set": {"status": "queued"}})

    ctx = worker_mod.Ctx(db=db, job=job, project_id=job["project_id"], payload={})
    with caplog.at_level(logging.WARNING, logger="worker"):
        ctx.progress(10, "working")

    assert f"lost lease on job {jid}" in caplog.text
