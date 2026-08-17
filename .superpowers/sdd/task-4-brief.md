### Task 4: Move export onto the queue

**Files:**
- Create: `backend/handlers/export.py`
- Modify: `backend/worker.py:_register_handlers`
- Modify: `backend/server.py:385-459` (`start_export` enqueues; delete `_run_export`)
- Modify: `backend/server.py` (add `POST /api/jobs/{jid}/cancel`, `GET /api/jobs/{jid}`)
- Create: `backend/tests/test_handler_export.py`

**Interfaces:**
- Consumes: `Ctx` from Task 2; `render_engine.render_export`, `cloudinary_svc.enabled`, `cloudinary_svc.upload_reel`, `compute_cut_state` (all existing).
- Produces: `handlers.export.run(ctx) -> dict` under kind `"export"`. Reads `ctx.payload` keys `caption_style` and `reel`. Writes the project's `export` sub-document exactly as `_run_export` did today, so the frontend needs no change.

This task deliberately keeps `render_engine.render_export` as the renderer. Swapping
renderers happens in Task 12, after the EDL model exists. One behaviour change at a
time.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_handler_export.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handlers.export'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/handlers/export.py`:

```python
"""Export job: cut, caption, master, optionally push to Cloudinary."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

import cloudinary_svc
import render_engine
import worker
from worker import Cancelled

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def project_dir(pid: str) -> Path:
    return DATA_DIR / pid


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(ctx) -> dict:
    from server import compute_cut_state  # imported lazily; server owns cut math

    doc = ctx.db.projects.find_one({"id": ctx.project_id})
    if not doc:
        raise RuntimeError(f"project {ctx.project_id} not found")

    style_key = ctx.payload["caption_style"]
    reel = ctx.payload["reel"]
    pdir = project_dir(ctx.project_id)
    out_path = pdir / "export.mp4"

    if ctx.cancelled():
        raise Cancelled()

    def cb(p):
        stage = "cutting" if p < 68 else ("captioning" if p < 90 else "mastering")
        ctx.progress(p, stage)
        ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
            "export.progress": p, "export.stage": stage,
        }})

    try:
        state = compute_cut_state({**doc, "reel_settings": reel})
        meta = render_engine.render_export(
            source=Path(doc["video_path"]),
            words=doc.get("words") or [],
            ranges=state["keep_ranges"],
            style_key=style_key,
            burn=reel["burn_captions"],
            work_dir=pdir / "work",
            out_path=out_path,
            aspect=reel["aspect"],
            cinematic=reel["cinematic"],
            karaoke=reel["karaoke"],
            zoom_intensity=reel["zoom_intensity"],
            punch_ins=reel.get("punch_ins", True),
            punch_sensitivity=reel.get("punch_sensitivity", 0.5),
            progress_cb=cb,
        )
    except Exception as e:
        ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
            "export": {"status": "error", "progress": 0,
                       "error": str(e)[:500], "stage": "failed"},
        }})
        raise

    cloud = {}
    if cloudinary_svc.enabled():
        try:
            cloud = cloudinary_svc.upload_reel(
                out_path, public_id=f"reel_{ctx.project_id}",
                reframe=reel["aspect"] == "9:16",
            )
        except Exception as e:
            cloud = {"error": str(e)[:300]}

    ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
        "export": {
            "status": "done", "progress": 100, "error": None, "stage": "done",
            "path": str(out_path), "meta": meta,
            "size": out_path.stat().st_size,
            "finished_at": _now_iso(),
        },
        "cloud": cloud,
    }})
    shutil.rmtree(pdir / "work", ignore_errors=True)
    return {"path": str(out_path)}


worker.HANDLERS["export"] = run
```

Modify `backend/worker.py:_register_handlers`:

```python
def _register_handlers() -> None:
    import handlers.transcribe  # noqa: F401
    import handlers.export      # noqa: F401
```

Modify `backend/server.py` — replace the body of `start_export` after validation:

```python
    projects.update_one({"id": pid}, {"$set": {
        "caption_style": body.caption_style,
        "reel_settings": reel,
        "export": {"status": "processing", "progress": 0, "error": None, "stage": "cutting"},
    }})
    jid = jobs.enqueue(db, pid, "export",
                       {"caption_style": body.caption_style, "reel": reel})
    return {"ok": True, "reel_settings": reel, "job_id": jid}
```

Delete `_run_export` entirely. Add two routes near the other `@api` routes:

```python
@api.get("/jobs/{jid}")
def get_job(jid: str):
    doc = db.jobs.find_one({"id": jid}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "job not found")
    return doc


@api.post("/jobs/{jid}/cancel")
def cancel_job(jid: str):
    if not db.jobs.find_one({"id": jid}, {"_id": 1}):
        raise HTTPException(404, "job not found")
    jobs.request_cancel(db, jid)
    return {"ok": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_export.py -v`
Expected: PASS, 3 passed

- [ ] **Step 5: Verify no threads remain**

Run: `cd backend && grep -n "threading" server.py`
Expected: no matches. Remove the now-unused `import threading`.

- [ ] **Step 6: Manual end-to-end check**

Start the worker in one terminal and the API in another, then upload a clip
through the UI and export it. Confirm the export completes and the file plays.

```bash
cd backend && ../.venv-local/Scripts/python.exe worker.py
```

- [ ] **Step 7: Commit**

```bash
git add backend/handlers/export.py backend/worker.py backend/server.py backend/tests/test_handler_export.py
git commit -m "feat: run export as a cancellable queued job"
```

---

