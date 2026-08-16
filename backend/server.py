import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

from fastapi import FastAPI, APIRouter, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import MongoClient

import cloudinary_svc
import jobs
import render_engine
from cut_state import DEFAULT_CUT_SETTINGS, DEFAULT_REEL, compute_cut_state, now_iso

client = MongoClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]
projects = db.projects

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Captions Editor API")
api = APIRouter(prefix="/api")


def project_dir(pid: str) -> Path:
    return DATA_DIR / pid


def get_project_or_404(pid: str) -> dict:
    doc = projects.find_one({"id": pid}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "project not found")
    return doc


def range_stream(path: Path, request: Request) -> StreamingResponse:
    file_size = path.stat().st_size
    range_header = request.headers.get("range")

    def iter_file(start: int, end: int, chunk_size: int = 1024 * 1024):
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = f.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {"Accept-Ranges": "bytes", "Content-Type": "video/mp4"}
    m = re.match(r"bytes=(\d+)-(\d*)", range_header) if range_header else None
    if m:
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else file_size - 1
        end = min(end, file_size - 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        headers["Content-Length"] = str(end - start + 1)
        return StreamingResponse(iter_file(start, end), status_code=206, headers=headers)
    headers["Content-Length"] = str(file_size)
    return StreamingResponse(iter_file(0, file_size - 1), headers=headers)


# ---------- Upload (chunked) ----------

class InitUpload(BaseModel):
    filename: str
    size: int


@api.post("/projects/upload/init")
def init_upload(body: InitUpload):
    if not re.search(r"\.(mp4|mov|m4v|webm|mkv|avi)$", body.filename, re.I):
        raise HTTPException(400, "unsupported file type")
    pid = str(uuid.uuid4())
    pdir = project_dir(pid)
    (pdir / "chunks").mkdir(parents=True)
    doc = {
        "id": pid,
        "filename": body.filename,
        "size": body.size,
        "status": "uploading",
        "error": None,
        "duration": 0,
        "width": 0,
        "height": 0,
        "words": [],
        "text": "",
        "cut_settings": dict(DEFAULT_CUT_SETTINGS),
        "reel_settings": dict(DEFAULT_REEL),
        "caption_style": "bold",
        "export": {"status": "idle", "progress": 0, "error": None},
        "created_at": now_iso(),
    }
    projects.insert_one(doc)
    return {"project_id": pid}


@api.post("/projects/{pid}/upload/chunk")
def upload_chunk(pid: str, index: int = Form(...), chunk: UploadFile = File(...)):
    get_project_or_404(pid)
    dest = project_dir(pid) / "chunks" / f"{index:06d}.part"
    with open(dest, "wb") as f:
        shutil.copyfileobj(chunk.file, f)
    return {"ok": True, "index": index}


@api.post("/projects/{pid}/upload/complete")
def complete_upload(pid: str):
    doc = get_project_or_404(pid)
    pdir = project_dir(pid)
    chunks_dir = pdir / "chunks"
    parts = sorted(chunks_dir.glob("*.part"))
    if not parts:
        raise HTTPException(400, "no chunks uploaded")
    ext = Path(doc["filename"]).suffix.lower() or ".mp4"
    raw_path = pdir / f"raw{ext}"
    with open(raw_path, "wb") as out:
        for p in parts:
            with open(p, "rb") as f:
                shutil.copyfileobj(f, out)
    shutil.rmtree(chunks_dir, ignore_errors=True)

    video_path = pdir / f"source{ext}"
    remux = subprocess.run(
        ["ffmpeg", "-y", "-i", str(raw_path), "-c", "copy", "-movflags", "+faststart", str(video_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if remux.returncode == 0:
        raw_path.unlink(missing_ok=True)
    else:
        raw_path.replace(video_path)

    try:
        info = render_engine.probe(video_path)
    except Exception:
        shutil.rmtree(pdir, ignore_errors=True)
        projects.delete_one({"id": pid})
        raise HTTPException(400, "file is not a valid video")

    projects.update_one({"id": pid}, {"$set": {
        "status": "transcribing",
        "video_path": str(video_path),
        "duration": info["duration"],
        "width": info["width"],
        "height": info["height"],
    }})
    try:
        render_engine.make_thumbnail(video_path, pdir / "thumb.jpg", min(1.0, info["duration"] / 2))
    except Exception:
        pass
    jobs.enqueue(db, pid, "transcribe")
    return {"ok": True, "status": "transcribing", "duration": info["duration"]}


# ---------- Project library ----------

@api.get("/projects")
def list_projects(limit: int = 30):
    docs = projects.find(
        {},
        {"_id": 0, "id": 1, "filename": 1, "duration": 1, "width": 1, "height": 1,
         "status": 1, "export": 1, "created_at": 1, "reel_settings": 1, "caption_style": 1},
    ).sort("created_at", -1).limit(max(1, min(100, limit)))
    items = []
    for d in docs:
        items.append({
            **d,
            "export_status": (d.get("export") or {}).get("status", "idle"),
            "has_thumb": (project_dir(d["id"]) / "thumb.jpg").exists(),
        })
    return {"projects": items}


@api.delete("/projects/{pid}")
def delete_project(pid: str):
    doc = get_project_or_404(pid)
    cloud = doc.get("cloud") or {}
    if cloud.get("public_id") and cloudinary_svc.enabled():
        try:
            cloudinary_svc.destroy(cloud["public_id"])
        except Exception:
            pass
    shutil.rmtree(project_dir(pid), ignore_errors=True)
    projects.delete_one({"id": pid})
    return {"ok": True}


@api.get("/projects/{pid}/thumbnail")
def get_thumbnail(pid: str):
    doc = get_project_or_404(pid)
    path = project_dir(pid) / "thumb.jpg"
    if not path.exists():
        source = Path(doc.get("video_path") or "")
        if not source.exists():
            raise HTTPException(404, "thumbnail not found")
        try:
            render_engine.make_thumbnail(source, path, min(1.0, (doc.get("duration") or 2) / 2))
        except Exception:
            raise HTTPException(404, "thumbnail not available")
    return FileResponse(path, media_type="image/jpeg")


# ---------- Project state ----------

@api.get("/projects/{pid}")
def get_project(pid: str):
    doc = get_project_or_404(pid)
    doc.pop("video_path", None)
    doc.setdefault("reel_settings", dict(DEFAULT_REEL))
    if doc["status"] == "ready":
        doc["cuts"] = compute_cut_state(doc)
    return doc


class CutSettings(BaseModel):
    pause_threshold: float = 0.8
    remove_fillers: bool = True
    disabled: list[str] = []


@api.post("/projects/{pid}/cuts")
def update_cuts(pid: str, body: CutSettings):
    doc = get_project_or_404(pid)
    if doc["status"] != "ready":
        raise HTTPException(400, "transcript not ready")
    settings = {
        "pause_threshold": max(0.3, min(3.0, body.pause_threshold)),
        "remove_fillers": body.remove_fillers,
        "disabled": body.disabled,
    }
    projects.update_one({"id": pid}, {"$set": {"cut_settings": settings}})
    doc["cut_settings"] = settings
    return compute_cut_state(doc)


class StyleBody(BaseModel):
    caption_style: str


@api.post("/projects/{pid}/style")
def set_style(pid: str, body: StyleBody):
    get_project_or_404(pid)
    if body.caption_style not in render_engine.CAPTION_STYLES:
        raise HTTPException(400, "unknown style")
    projects.update_one({"id": pid}, {"$set": {"caption_style": body.caption_style}})
    return {"ok": True}


class ReelSettings(BaseModel):
    aspect: str = "9:16"
    cinematic: bool = True
    karaoke: bool = True
    zoom_intensity: float = 1.0
    punch_ins: bool = True
    punch_sensitivity: float = 0.5
    burn_captions: bool = True


def _clean_reel(body: ReelSettings) -> dict:
    if body.aspect not in ("9:16", "original"):
        raise HTTPException(400, "aspect must be 9:16 or original")
    return {
        "aspect": body.aspect,
        "cinematic": body.cinematic,
        "karaoke": body.karaoke,
        "zoom_intensity": max(0.2, min(1.6, body.zoom_intensity)),
        "punch_ins": body.punch_ins,
        "punch_sensitivity": max(0.0, min(1.0, body.punch_sensitivity)),
        "burn_captions": body.burn_captions,
    }


@api.post("/projects/{pid}/reel")
def set_reel(pid: str, body: ReelSettings):
    doc = get_project_or_404(pid)
    settings = _clean_reel(body)
    projects.update_one({"id": pid}, {"$set": {"reel_settings": settings}})
    doc["reel_settings"] = settings
    return {"reel_settings": settings, "cuts": compute_cut_state(doc) if doc["status"] == "ready" else None}


# ---------- Video streaming ----------

@api.get("/projects/{pid}/video")
def stream_video(pid: str, request: Request):
    doc = projects.find_one({"id": pid})
    if not doc or not doc.get("video_path"):
        raise HTTPException(404, "video not found")
    path = Path(doc["video_path"])
    if not path.exists():
        raise HTTPException(404, "video not found")
    return range_stream(path, request)


@api.get("/projects/{pid}/export/video")
def stream_export(pid: str, request: Request):
    doc = get_project_or_404(pid)
    export = doc.get("export") or {}
    path = Path(export.get("path") or "")
    if export.get("status") != "done" or not path.exists():
        raise HTTPException(404, "export not ready")
    return range_stream(path, request)


# ---------- Export ----------

class ExportBody(BaseModel):
    caption_style: str = "bold"
    burn_captions: bool = True
    aspect: str = "original"
    cinematic: bool = True
    karaoke: bool = True
    zoom_intensity: float = 1.0
    punch_ins: bool = True
    punch_sensitivity: float = 0.5


@api.post("/projects/{pid}/export")
def start_export(pid: str, body: ExportBody):
    doc = get_project_or_404(pid)
    if doc["status"] != "ready":
        raise HTTPException(400, "transcript not ready")
    if (doc.get("export") or {}).get("status") == "processing":
        raise HTTPException(400, "export already running")
    if body.caption_style not in render_engine.CAPTION_STYLES:
        raise HTTPException(400, "unknown style")
    reel = _clean_reel(ReelSettings(
        aspect=body.aspect, cinematic=body.cinematic, karaoke=body.karaoke,
        zoom_intensity=body.zoom_intensity, punch_ins=body.punch_ins,
        punch_sensitivity=body.punch_sensitivity,
        burn_captions=body.burn_captions,
    ))
    projects.update_one({"id": pid}, {"$set": {
        "caption_style": body.caption_style,
        "reel_settings": reel,
        "export": {"status": "processing", "progress": 0, "error": None, "stage": "cutting"},
    }})
    jid = jobs.enqueue(db, pid, "export",
                       {"caption_style": body.caption_style, "reel": reel})
    return {"ok": True, "reel_settings": reel, "job_id": jid}


@api.get("/projects/{pid}/export/download")
def download_export(pid: str):
    doc = get_project_or_404(pid)
    export = doc.get("export") or {}
    if export.get("status") != "done":
        raise HTTPException(404, "export not ready")
    path = Path(export["path"])
    if not path.exists():
        raise HTTPException(404, "export file missing")
    stem = Path(doc["filename"]).stem
    return FileResponse(path, media_type="video/mp4", filename=f"{stem}_reel.mp4")


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


@api.get("/styles")
def list_styles():
    return {"styles": list(render_engine.CAPTION_STYLES.keys())}


@api.get("/cloudinary/status")
def cloudinary_status():
    return {
        "enabled": cloudinary_svc.enabled(),
        "cloud_name": os.environ.get("CLOUDINARY_CLOUD_NAME") or None,
    }


@api.get("/")
def health():
    return {"status": "ok"}


app.include_router(api)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
