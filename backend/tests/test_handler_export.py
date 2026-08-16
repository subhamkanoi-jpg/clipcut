import os
import sys

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs as jobs_mod
import worker as worker_mod
import handlers.export as eh

REEL = {
    "aspect": "9:16", "cinematic": True, "karaoke": True, "zoom_intensity": 1.0,
    "punch_ins": True, "punch_sensitivity": 0.5, "burn_captions": True,
}


@pytest.fixture
def db():
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    name = "clipcut_test"
    client.drop_database(name)
    yield client[name]
    client.drop_database(name)


def _project(db, pid="p1"):
    db.projects.insert_one({
        "id": pid, "status": "ready", "video_path": "/tmp/source.mp4",
        "duration": 10.0, "width": 1080, "height": 1920,
        "words": [{"text": "hi", "start": 0.0, "end": 0.4, "type": "word"}],
        "cut_settings": {"pause_threshold": 0.8, "remove_fillers": True, "disabled": []},
        "reel_settings": dict(REEL), "caption_style": "bold",
        "export": {"status": "idle", "progress": 0, "error": None},
    })


def test_export_writes_done_state(db, monkeypatch, tmp_path):
    _project(db)
    out = tmp_path / "export.mp4"
    out.write_bytes(b"video-bytes")

    monkeypatch.setattr(eh, "project_dir", lambda pid: tmp_path)
    monkeypatch.setattr(
        eh.render_engine, "render_export",
        lambda **kw: {"width": 1080, "height": 1920, "duration": 9.0},
    )
    monkeypatch.setattr(eh.cloudinary_svc, "enabled", lambda: False)

    jobs_mod.enqueue(db, "p1", "export",
                     {"caption_style": "neon", "reel": dict(REEL)})
    worker_mod.run_once(db, "w1")

    exp = db.projects.find_one({"id": "p1"})["export"]
    assert exp["status"] == "done"
    assert exp["progress"] == 100
    assert exp["size"] == len(b"video-bytes")


def test_export_failure_records_error_not_stuck_processing(db, monkeypatch, tmp_path):
    _project(db, "p2")
    monkeypatch.setattr(eh, "project_dir", lambda pid: tmp_path)

    def boom(**kw):
        raise RuntimeError("ffmpeg exited 1")

    monkeypatch.setattr(eh.render_engine, "render_export", boom)
    jobs_mod.enqueue(db, "p2", "export",
                     {"caption_style": "bold", "reel": dict(REEL)})
    worker_mod.run_once(db, "w1")

    exp = db.projects.find_one({"id": "p2"})["export"]
    assert exp["status"] == "error"
    assert "ffmpeg" in exp["error"]


def test_export_honours_cancellation_before_render(db, monkeypatch, tmp_path):
    _project(db, "p3")
    monkeypatch.setattr(eh, "project_dir", lambda pid: tmp_path)
    called = {"n": 0}

    def counting(**kw):
        called["n"] += 1
        return {}

    monkeypatch.setattr(eh.render_engine, "render_export", counting)
    jid = jobs_mod.enqueue(db, "p3", "export",
                           {"caption_style": "bold", "reel": dict(REEL)})
    jobs_mod.request_cancel(db, jid)
    worker_mod.run_once(db, "w1")

    assert called["n"] == 0
    assert db.jobs.find_one({"id": jid})["status"] == "cancelled"
    exp = db.projects.find_one({"id": "p3"})["export"]
    assert exp["status"] == "cancelled"
    assert exp["progress"] == 0
    assert exp["error"] is None
    assert exp["stage"] == "cancelled"
