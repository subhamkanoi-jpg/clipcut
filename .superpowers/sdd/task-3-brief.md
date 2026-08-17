### Task 3: Move transcription onto the queue

**Files:**
- Create: `backend/handlers/__init__.py`
- Create: `backend/handlers/transcribe.py`
- Modify: `backend/worker.py` (import the handler module so it registers)
- Modify: `backend/server.py:200-222` (replace `threading.Thread` with `jobs.enqueue`; delete `_run_transcription`)
- Create: `backend/tests/test_handler_transcribe.py`

**Interfaces:**
- Consumes: `Ctx`, `HANDLERS` from Task 2; `transcription.transcribe_video` (existing).
- Produces: `handlers.transcribe.run(ctx) -> dict` registered under kind `"transcribe"`. Sets the project's `status` to `ready` or `error` and stores `words` and `text`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_handler_transcribe.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_transcribe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handlers'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/handlers/__init__.py`:

```python
"""Job handlers. Importing a module here registers it in worker.HANDLERS."""
```

Create `backend/handlers/transcribe.py`:

```python
"""Transcription job: Scribe -> words on the project document."""

from pathlib import Path

import transcription
import worker


def run(ctx) -> dict:
    doc = ctx.db.projects.find_one({"id": ctx.project_id})
    if not doc:
        raise RuntimeError(f"project {ctx.project_id} not found")
    ctx.progress(10, "transcribing")
    try:
        payload = transcription.transcribe_video(Path(doc["video_path"]))
    except Exception as e:
        ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
            "status": "error", "error": str(e)[:500],
        }})
        raise
    words = payload.get("words") or []
    ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
        "status": "ready",
        "words": words,
        "text": payload.get("text") or "",
        "error": None,
    }})
    return {"word_count": len(words)}


worker.HANDLERS["transcribe"] = run
```

Modify `backend/worker.py` — add below the `import jobs as jobs_mod` line:

```python
import jobs as jobs_mod

# Importing handler modules registers them in HANDLERS. Keep after HANDLERS exists.
def _register_handlers() -> None:
    import handlers.transcribe  # noqa: F401
```

and call `_register_handlers()` as the first statement inside `main()`.

Note: `HANDLERS` must be defined before `_register_handlers` runs, which is why
registration happens inside `main()` rather than at import time. Tests import
`handlers.transcribe` directly, which registers it for them.

Modify `backend/server.py` — delete the `_run_transcription` function
(lines 208-222) and replace the thread spawn at the end of `complete_upload`:

```python
    # was: threading.Thread(target=_run_transcription, args=(pid,), daemon=True).start()
    jobs.enqueue(db, pid, "transcribe")
    return {"ok": True, "status": "transcribing", "duration": info["duration"]}
```

Add `import jobs` to the imports at the top of `backend/server.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_transcribe.py -v`
Expected: PASS, 2 passed

- [ ] **Step 5: Verify the API no longer spawns threads**

Run: `cd backend && grep -n "threading.Thread" server.py`
Expected: one remaining match, in `start_export` — removed in Task 4.

- [ ] **Step 6: Commit**

```bash
git add backend/handlers backend/worker.py backend/server.py backend/tests/test_handler_transcribe.py
git commit -m "feat: run transcription as a queued job"
```

---

