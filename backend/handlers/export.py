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
