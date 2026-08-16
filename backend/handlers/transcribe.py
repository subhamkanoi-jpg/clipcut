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
