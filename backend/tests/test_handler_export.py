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


def _project(db, pid="p1", reel=None):
    db.projects.insert_one({
        "id": pid, "status": "ready", "video_path": "/tmp/source.mp4",
        "duration": 10.0, "width": 1080, "height": 1920,
        "words": [{"text": "hi", "start": 0.0, "end": 0.4, "type": "word"}],
        "cut_settings": {"pause_threshold": 0.8, "remove_fillers": True, "disabled": []},
        "reel_settings": dict(reel or REEL), "caption_style": "bold",
        "export": {"status": "idle", "progress": 0, "error": None},
    })


def test_export_writes_done_state(db, monkeypatch, tmp_path):
    _project(db)
    out = tmp_path / "export.mp4"
    out.write_bytes(b"video-bytes")

    monkeypatch.setattr(eh, "project_dir", lambda pid: tmp_path)
    monkeypatch.setattr(
        eh.render_plan, "render",
        lambda *a, **kw: {"width": 1080, "height": 1920, "duration": 9.0},
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

    def boom(*a, **kw):
        raise RuntimeError("ffmpeg exited 1")

    monkeypatch.setattr(eh.render_plan, "render", boom)
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

    def counting(*a, **kw):
        called["n"] += 1
        return {}

    monkeypatch.setattr(eh.render_plan, "render", counting)
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


def test_export_9_16_calls_subject_center_and_reaches_plan(db, monkeypatch, tmp_path):
    _project(db, "p4")
    monkeypatch.setattr(eh, "project_dir", lambda pid: tmp_path)
    (tmp_path / "export.mp4").write_bytes(b"video-bytes")

    calls = {"n": 0, "path": None}
    captured = {}

    def fake_subject_center(video_path):
        calls["n"] += 1
        calls["path"] = video_path
        return 0.27

    def fake_render(edl, *a, **kw):
        captured["edl"] = edl
        return {"width": 1080, "height": 1920, "duration": 9.0}

    monkeypatch.setattr(eh.reframe, "subject_center", fake_subject_center)
    monkeypatch.setattr(eh.render_plan, "render", fake_render)
    monkeypatch.setattr(eh.cloudinary_svc, "enabled", lambda: False)

    jobs_mod.enqueue(db, "p4", "export",
                     {"caption_style": "bold", "reel": dict(REEL)})
    worker_mod.run_once(db, "w1")

    assert calls["n"] == 1
    assert str(calls["path"]) == "/tmp/source.mp4" or str(calls["path"]).endswith("source.mp4")
    assert captured["edl"]["reframe"]["center_x"] == 0.27

    doc = db.projects.find_one({"id": "p4"})
    assert doc["subject_center_x"] == 0.27
    assert doc["export"]["status"] == "done"


def test_export_original_aspect_skips_subject_center(db, monkeypatch, tmp_path):
    reel = {**REEL, "aspect": "original"}
    _project(db, "p5", reel=reel)
    monkeypatch.setattr(eh, "project_dir", lambda pid: tmp_path)
    (tmp_path / "export.mp4").write_bytes(b"video-bytes")

    calls = {"n": 0}

    def fake_subject_center(video_path):
        calls["n"] += 1
        return 0.27

    monkeypatch.setattr(eh.reframe, "subject_center", fake_subject_center)
    monkeypatch.setattr(
        eh.render_plan, "render",
        lambda *a, **kw: {"width": 1920, "height": 1080, "duration": 9.0},
    )
    monkeypatch.setattr(eh.cloudinary_svc, "enabled", lambda: False)

    jobs_mod.enqueue(db, "p5", "export",
                     {"caption_style": "bold", "reel": dict(reel)})
    worker_mod.run_once(db, "w1")

    assert calls["n"] == 0
    doc = db.projects.find_one({"id": "p5"})
    assert "subject_center_x" not in doc
    assert doc["export"]["status"] == "done"


def test_export_cleans_edit_work_dir_but_keeps_edl(db, monkeypatch, tmp_path):
    _project(db, "p6")
    monkeypatch.setattr(eh, "project_dir", lambda pid: tmp_path)

    edit_dir = tmp_path / "edit"
    work_dir = edit_dir / "work"
    work_dir.mkdir(parents=True)
    (work_dir / "seg_0000.mp4").write_bytes(b"scratch")
    (edit_dir / "edl.json").write_text("{}", encoding="utf-8")
    (tmp_path / "export.mp4").write_bytes(b"video-bytes")

    monkeypatch.setattr(
        eh.render_plan, "render",
        lambda *a, **kw: {"width": 1080, "height": 1920, "duration": 9.0},
    )
    monkeypatch.setattr(eh.cloudinary_svc, "enabled", lambda: False)

    jobs_mod.enqueue(db, "p6", "export",
                     {"caption_style": "bold", "reel": dict(REEL)})
    worker_mod.run_once(db, "w1")

    assert not work_dir.exists()
    assert (edit_dir / "edl.json").exists()
