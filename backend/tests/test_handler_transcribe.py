import os
import sys
from pathlib import Path

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs as jobs_mod
import worker as worker_mod
import handlers.transcribe as th


@pytest.fixture
def db():
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    name = "clipcut_test"
    client.drop_database(name)
    yield client[name]
    client.drop_database(name)


def test_transcribe_stores_words_and_marks_ready(db, monkeypatch, tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"not-a-real-video")
    db.projects.insert_one({
        "id": "p1", "status": "transcribing", "video_path": str(video),
    })
    monkeypatch.setattr(
        th.transcription, "transcribe_video",
        lambda path: {"words": [{"text": "hi", "start": 0.0, "end": 0.3, "type": "word"}],
                      "text": "hi"},
    )
    jid = jobs_mod.enqueue(db, "p1", "transcribe")
    worker_mod.run_once(db, "w1")

    doc = db.projects.find_one({"id": "p1"})
    assert doc["status"] == "ready"
    assert doc["text"] == "hi"
    assert len(doc["words"]) == 1
    assert db.jobs.find_one({"id": jid})["status"] == "done"


def test_transcribe_failure_marks_project_error(db, monkeypatch, tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"x")
    db.projects.insert_one({
        "id": "p2", "status": "transcribing", "video_path": str(video),
    })

    def boom(path):
        raise RuntimeError("Scribe returned 401")

    monkeypatch.setattr(th.transcription, "transcribe_video", boom)
    jobs_mod.enqueue(db, "p2", "transcribe")
    worker_mod.run_once(db, "w1")

    doc = db.projects.find_one({"id": "p2"})
    assert doc["status"] == "error"
    assert "401" in doc["error"]


def test_transcribe_success_enqueues_a_plan_job(db, monkeypatch, tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"x")
    db.projects.insert_one({"id": "p9", "status": "transcribing", "video_path": str(video)})
    monkeypatch.setattr(
        th.transcription, "transcribe_video",
        lambda path: {"words": [{"text": "hi", "start": 0.0, "end": 0.3, "type": "word"}], "text": "hi"},
    )
    jobs_mod.enqueue(db, "p9", "transcribe")
    worker_mod.run_once(db, "w1")
    assert db.jobs.find_one({"project_id": "p9", "kind": "plan"}) is not None
