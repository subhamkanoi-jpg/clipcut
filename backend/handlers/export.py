"""Export job: cut, caption, master, optionally push to Cloudinary."""

import shutil
from pathlib import Path

import cloudinary_svc
import reframe
import worker
from cut_state import compute_cut_state, now_iso
from errors import Cancelled
from plan import assemble, materialize, render_plan

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def project_dir(pid: str) -> Path:
    return DATA_DIR / pid


def run(ctx) -> dict:
    doc = ctx.db.projects.find_one({"id": ctx.project_id})
    if not doc:
        raise RuntimeError(f"project {ctx.project_id} not found")

    style_key = ctx.payload["caption_style"]
    reel = ctx.payload["reel"]
    pdir = project_dir(ctx.project_id)
    out_path = pdir / "export.mp4"

    if ctx.cancelled():
        ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
            "export": {"status": "cancelled", "progress": 0,
                       "error": None, "stage": "cancelled"},
        }})
        raise Cancelled()

    def cb(p, stage):
        ctx.progress(p, stage)
        ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
            "export.progress": p, "export.stage": stage,
        }})

    try:
        if reel.get("aspect") == "9:16":
            cb(5, "reframing")
            subject_center_x = reframe.subject_center(Path(doc["video_path"]))
            doc["subject_center_x"] = subject_center_x
            ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
                "subject_center_x": subject_center_x,
            }})

        state = compute_cut_state({**doc, "reel_settings": reel})
        edl = assemble.from_project({**doc, "caption_style": style_key}, state)
        meta = render_plan.render(
            edl, pdir, out_path,
            words=doc.get("words") or [],
            progress_cb=cb,
            cancel_cb=ctx.cancelled,
        )
    except Cancelled:
        # Cancelled subclasses Exception, so it must be caught here, ahead of
        # the generic `except Exception` below -- otherwise a cancel raised
        # mid-render (render_plan's own cancel_cb check) falls into that
        # branch instead, which sets status "error" with str(Cancelled()),
        # an EMPTY string, leaving the project showing "Export failed: "
        # with nothing after it while the job doc correctly says cancelled.
        # Mirror the same shape the pre-render cancel path above uses.
        ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
            "export": {"status": "cancelled", "progress": 0,
                       "error": None, "stage": "cancelled"},
        }})
        raise
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
            "finished_at": now_iso(),
        },
        "cloud": cloud,
    }})
    shutil.rmtree(materialize.edit_dir(pdir) / "work", ignore_errors=True)
    return {"path": str(out_path)}


worker.HANDLERS["export"] = run
