import os
import sys
from pathlib import Path

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

import jobs as jobs_mod
import worker as worker_mod
import handlers.plan as ph


@pytest.fixture
def db():
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    name = "clipcut_test"
    client.drop_database(name)
    yield client[name]
    client.drop_database(name)


def _project(db, tmp_path, pid="p1"):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    db.projects.insert_one({
        "id": pid, "status": "ready", "video_path": str(src),
        "duration": 6.0, "width": 1080, "height": 1920,
        "words": [
            {"text": "This", "start": 0.0, "end": 0.3, "type": "word"},
            {"text": "laptop", "start": 0.4, "end": 0.9, "type": "word"},
            {"text": "everything", "start": 1.0, "end": 1.6, "type": "word"},
        ],
        "text": "This laptop everything",
        "cut_settings": {"pause_threshold": 0.8, "remove_fillers": True, "disabled": []},
        "reel_settings": {"aspect": "9:16", "cinematic": True, "karaoke": True,
                          "zoom_intensity": 1.0, "punch_ins": True,
                          "punch_sensitivity": 0.5, "burn_captions": True},
        "caption_style": "bold",
    })
    return pid


def test_plan_job_stores_a_plan_with_overlays(db, tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "project_dir", lambda pid: tmp_path)
    _project(db, tmp_path)
    jobs_mod.enqueue(db, "p1", "plan")
    worker_mod.run_once(db, "w1")

    doc = db.projects.find_one({"id": "p1"})
    assert doc["plan_status"] == "ready"
    # ClaudeCliProvider is tried first (Task 8). The `claude` binary is a real
    # install on this machine and is not stubbed in this handler-level test, so
    # it may or may not produce a valid picks.json against this fixture project
    # depending on the environment: "claude" if it did, "heuristic" if the
    # chain fell through. Either is a correct outcome here.
    assert doc["plan_provider"] in ("heuristic", "claude")
    plan = doc["plan"]
    assert plan["version"] == 2
    assert isinstance(plan["overlays"], list)
    assert len(plan["overlays"]) >= 1


def test_regenerate_preserves_locked_overlays(db, tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "project_dir", lambda pid: tmp_path)
    _project(db, tmp_path)
    jobs_mod.enqueue(db, "p1", "plan")
    worker_mod.run_once(db, "w1")

    plan = db.projects.find_one({"id": "p1"})["plan"]
    locked_id = plan["overlays"][0]["id"]
    db.projects.update_one({"id": "p1", "plan.overlays.id": locked_id},
                           {"$set": {"plan.overlays.$.locked": True}})

    jobs_mod.enqueue(db, "p1", "plan", {"regenerate": True})
    worker_mod.run_once(db, "w1")

    ids = [o["id"] for o in db.projects.find_one({"id": "p1"})["plan"]["overlays"]]
    assert locked_id in ids


def test_missing_project_fails_job(db, tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "project_dir", lambda pid: tmp_path)
    jid = jobs_mod.enqueue(db, "ghost", "plan")
    worker_mod.run_once(db, "w1")
    assert db.jobs.find_one({"id": jid})["status"] == "error"


def test_plan_job_failure_resets_plan_status(db, tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "project_dir", lambda pid: tmp_path)
    _project(db, tmp_path)
    db.projects.update_one({"id": "p1"}, {"$set": {"plan_status": "planning"}})
    monkeypatch.setattr(ph, "compute_cut_state",
                        lambda doc: (_ for _ in ()).throw(RuntimeError("boom")))
    jobs_mod.enqueue(db, "p1", "plan")
    worker_mod.run_once(db, "w1")
    assert db.projects.find_one({"id": "p1"})["plan_status"] == "error"
