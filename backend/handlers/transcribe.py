"""Transcription job: Scribe -> words on the project document."""

from pathlib import Path

import jobs
import transcription
import worker
from errors import Cancelled


def run(ctx) -> dict:
    doc = ctx.db.projects.find_one({"id": ctx.project_id})
    if not doc:
        raise RuntimeError(f"project {ctx.project_id} not found")
    if ctx.cancelled():
        # The Scribe call itself isn't interruptible once started, so this is
        # the only point where checking is actually useful -- otherwise a
        # cancelled transcribe just runs to completion and gets recorded as
        # "done".
        raise Cancelled()
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
    jobs.enqueue(ctx.db, ctx.project_id, "plan")
    return {"word_count": len(words)}


worker.HANDLERS["transcribe"] = run
