"""HTTP-level coverage for the plan routes added to server.py in Task 9:
POST /api/projects/{pid}/plan and GET /api/projects/{pid}/plan.

server.py binds its Mongo client/db/collection at *module import time* using
the real .env (DB_NAME=clipcut -- production). To keep these tests off that
database entirely, we import server once, then monkeypatch the module-level
`db`/`projects` handles to point at `clipcut_test` before driving requests
through a FastAPI TestClient. The route handlers reference the module
globals `db`/`projects` (and pass `db` straight into `jobs.enqueue`), so
patching those two names is sufficient -- no production code changes needed.
"""
import os
import sys

import pytest
from pymongo import MongoClient
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server


@pytest.fixture
def db():
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    name = "clipcut_test"
    client.drop_database(name)
    yield client[name]
    client.drop_database(name)


@pytest.fixture
def tc(db, monkeypatch):
    monkeypatch.setattr(server, "db", db)
    monkeypatch.setattr(server, "projects", db.projects)
    return TestClient(server.app)


def _project(db, pid="p1", **extra):
    doc = {
        "id": pid, "status": "ready", "video_path": "/tmp/source.mp4",
        "duration": 10.0, "width": 1080, "height": 1920,
        "words": [], "text": "",
        "cut_settings": {"pause_threshold": 0.8, "remove_fillers": True, "disabled": []},
        "reel_settings": {"aspect": "9:16", "cinematic": True, "karaoke": True,
                          "zoom_intensity": 1.0, "punch_ins": True,
                          "punch_sensitivity": 0.5, "burn_captions": True},
        "caption_style": "bold",
        "export": {"status": "idle", "progress": 0, "error": None},
    }
    doc.update(extra)
    db.projects.insert_one(doc)


def test_post_plan_enqueues_and_returns_job_id(db, tc):
    _project(db, "p1")
    resp = tc.post("/api/projects/p1/plan", json={"regenerate": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "job_id" in body and body["job_id"]

    job = db.jobs.find_one({"id": body["job_id"]})
    assert job is not None
    assert job["kind"] == "plan"
    assert job["project_id"] == "p1"

    doc = db.projects.find_one({"id": "p1"})
    assert doc["plan_status"] == "planning"


def test_post_plan_passes_regenerate_flag(db, tc):
    _project(db, "p1")
    resp = tc.post("/api/projects/p1/plan", json={"regenerate": True})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    job = db.jobs.find_one({"id": job_id})
    assert job["payload"]["regenerate"] is True


def test_post_plan_404_when_project_missing(db, tc):
    resp = tc.post("/api/projects/ghost/plan", json={"regenerate": False})
    assert resp.status_code == 404


def test_get_plan_returns_stored_plan(db, tc):
    plan = {"version": 2, "overlays": []}
    _project(db, "p1", plan=plan, plan_provider="heuristic", plan_status="ready")
    resp = tc.get("/api/projects/p1/plan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"] == plan
    assert body["provider"] == "heuristic"
    assert body["status"] == "ready"


def test_get_plan_404_when_no_plan(db, tc):
    _project(db, "p1")
    resp = tc.get("/api/projects/p1/plan")
    assert resp.status_code == 404


def test_get_plan_404_when_project_missing(db, tc):
    resp = tc.get("/api/projects/ghost/plan")
    assert resp.status_code == 404


# --- PATCH /projects/{pid}/plan/overlays/{oid} (added in the b2 review UI) ---

def _plan_with_overlay(**ov_extra):
    ov = {
        "id": "ov1", "kind": "broll", "start_in_output": 1.0, "duration": 2.0,
        "file": "/tmp/broll.mp4", "query": "laptop", "source": "mixkit",
        "enabled": True, "locked": False,
    }
    ov.update(ov_extra)
    return {"version": 2, "overlays": [ov], "ranges": [], "total_duration_s": 10.0}


def test_patch_overlay_toggles_enabled(db, tc):
    _project(db, "p1", plan=_plan_with_overlay(), plan_status="ready")
    resp = tc.patch("/api/projects/p1/plan/overlays/ov1", json={"enabled": False})
    assert resp.status_code == 200
    stored = db.projects.find_one({"id": "p1"})["plan"]["overlays"][0]
    assert stored["enabled"] is False


def test_patch_overlay_toggles_locked(db, tc):
    _project(db, "p1", plan=_plan_with_overlay(), plan_status="ready")
    resp = tc.patch("/api/projects/p1/plan/overlays/ov1", json={"locked": True})
    assert resp.status_code == 200
    assert db.projects.find_one({"id": "p1"})["plan"]["overlays"][0]["locked"] is True


def test_patch_overlay_query_change_clears_file(db, tc):
    _project(db, "p1", plan=_plan_with_overlay(), plan_status="ready")
    resp = tc.patch("/api/projects/p1/plan/overlays/ov1", json={"query": "desk"})
    assert resp.status_code == 200
    stored = db.projects.find_one({"id": "p1"})["plan"]["overlays"][0]
    assert stored["query"] == "desk"
    assert stored["file"] is None  # cleared so the next render re-fetches


def test_patch_overlay_same_query_keeps_file(db, tc):
    _project(db, "p1", plan=_plan_with_overlay(), plan_status="ready")
    resp = tc.patch("/api/projects/p1/plan/overlays/ov1", json={"query": "laptop"})
    assert resp.status_code == 200
    assert db.projects.find_one({"id": "p1"})["plan"]["overlays"][0]["file"] == "/tmp/broll.mp4"


def test_patch_overlay_leaves_other_fields_untouched(db, tc):
    _project(db, "p1", plan=_plan_with_overlay(), plan_status="ready")
    tc.patch("/api/projects/p1/plan/overlays/ov1", json={"enabled": False})
    stored = db.projects.find_one({"id": "p1"})["plan"]["overlays"][0]
    assert stored["locked"] is False
    assert stored["start_in_output"] == 1.0
    assert stored["query"] == "laptop"


def test_patch_overlay_404_when_overlay_missing(db, tc):
    _project(db, "p1", plan=_plan_with_overlay(), plan_status="ready")
    resp = tc.patch("/api/projects/p1/plan/overlays/nope", json={"enabled": False})
    assert resp.status_code == 404


def test_patch_overlay_404_when_no_plan(db, tc):
    _project(db, "p1")
    resp = tc.patch("/api/projects/p1/plan/overlays/ov1", json={"enabled": False})
    assert resp.status_code == 404


def test_patch_overlay_404_when_project_missing(db, tc):
    resp = tc.patch("/api/projects/ghost/plan/overlays/ov1", json={"enabled": False})
    assert resp.status_code == 404
