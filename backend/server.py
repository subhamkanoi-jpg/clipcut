import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from psycopg import connect
from psycopg.rows import dict_row
import requests
from types import SimpleNamespace

import cuts as cuts_mod
import render_engine
import transcription
import zooms

app = FastAPI(title="ClipCut Cloud API")
api = APIRouter()
BLOB_BASE_URL = "https://blob.vercel-storage.com"

DEFAULT_CUT_SETTINGS = {"pause_threshold": 0.8, "remove_fillers": True, "disabled": []}
DEFAULT_REEL = {"aspect": "9:16", "cinematic": True, "karaoke": True, "zoom_intensity": 1.0, "punch_ins": True, "punch_sensitivity": 0.5, "burn_captions": True}
MAX_UPLOAD = 500 * 1024 * 1024


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def db():
    return connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def query_one(sql, params=()):
    with db() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql, params=()):
    with db() as conn, conn.cursor() as cur:
        cur.execute(sql, params)


def project_token(request: Request):
    token = request.headers.get("x-project-token", "") or request.query_params.get("access_token", "")
    if len(token) < 32:
        raise HTTPException(401, "project access token required")
    return token


def get_project(pid, request):
    doc = query_one("SELECT * FROM clipcut_projects WHERE id=%s AND access_token_hash=%s", (pid, token_hash(project_token(request))))
    if not doc:
        raise HTTPException(404, "project not found")
    return normalize(doc)


def normalize(doc):
    if not doc:
        return doc
    doc["id"] = str(doc["id"])
    doc["size"] = doc.pop("size_bytes")
    doc["words"] = doc.pop("transcript") or []
    doc["text"] = doc.pop("transcript_text") or ""
    doc["export"] = doc.pop("export_state") or {"status": "idle", "progress": 0}
    doc["created_at"] = doc["created_at"].isoformat() if hasattr(doc["created_at"], "isoformat") else doc["created_at"]
    doc.pop("access_token_hash", None)
    return doc


def compute_cut_state(doc):
    settings = doc.get("cut_settings") or DEFAULT_CUT_SETTINGS
    words, duration = doc.get("words") or [], doc.get("duration") or 0
    spans = cuts_mod.compute_spans(words, duration, settings["pause_threshold"], settings["remove_fillers"])
    disabled = set(settings.get("disabled") or [])
    ranges = cuts_mod.keep_ranges(duration, spans, disabled)
    for span in spans:
        span["disabled"] = span["id"] in disabled
    kept = sum(b - a for a, b in ranges)
    reel = doc.get("reel_settings") or DEFAULT_REEL
    return {"spans": spans, "keep_ranges": ranges, "kept_duration": round(kept, 2), "removed_duration": round(max(0, duration-kept), 2), "settings": settings, "moves": zooms.plan(words, ranges, reel.get("zoom_intensity", 1), reel.get("punch_ins", True), reel.get("punch_sensitivity", .5)) if reel.get("cinematic") else []}


def _blob_headers(content_type=None):
    headers = {"Authorization": f"Bearer {os.environ['BLOB_READ_WRITE_TOKEN']}"}
    if content_type:
        headers["x-content-type"] = content_type
        headers["Content-Type"] = content_type
    return headers


def blob_put(pathname, data, content_type):
    response = requests.put(
        f"{BLOB_BASE_URL}/{pathname}",
        data=data,
        headers={**_blob_headers(content_type), "x-allow-overwrite": "true"},
        timeout=120,
    )
    if response.status_code >= 300:
        raise RuntimeError(f"Blob upload failed: {response.status_code} {response.text[:300]}")
    return SimpleNamespace(pathname=pathname)


def blob_bytes(pathname):
    response = requests.get(
        f"{BLOB_BASE_URL}/{pathname}",
        headers=_blob_headers(),
        timeout=120,
    )
    if response.status_code == 404:
        raise HTTPException(404, "media not found")
    if response.status_code >= 300:
        raise RuntimeError(f"Blob download failed: {response.status_code}")
    return response.content


def media_response(data, content_type="video/mp4", filename=None):
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"}
    if filename:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(content=data, media_type=content_type, headers=headers)


class InitUpload(BaseModel):
    filename: str
    size: int


@api.post("/projects/upload/init")
def init_upload(body: InitUpload):
    if not re.search(r"\.(mp4|mov|m4v|webm|mkv|avi)$", body.filename, re.I):
        raise HTTPException(400, "unsupported file type")
    if body.size <= 0 or body.size > MAX_UPLOAD:
        raise HTTPException(400, "video must be between 1 byte and 500 MB")
    pid, token = str(uuid.uuid4()), uuid.uuid4().hex + uuid.uuid4().hex
    execute("INSERT INTO clipcut_projects (id,access_token_hash,filename,size_bytes,status,cut_settings,reel_settings) VALUES (%s,%s,%s,%s,'uploading',%s::jsonb,%s::jsonb)", (pid, token_hash(token), body.filename[:240], body.size, json.dumps(DEFAULT_CUT_SETTINGS), json.dumps(DEFAULT_REEL)))
    return {"project_id": pid, "project_token": token}


@api.post("/projects/{pid}/upload/chunk")
def upload_chunk(pid: str, request: Request, index: int = Form(...), chunk: UploadFile = File(...)):
    get_project(pid, request)
    if index < 0 or index > 1000:
        raise HTTPException(400, "invalid chunk index")
    data = chunk.file.read(6 * 1024 * 1024)
    blob_put(f"clipcut/{pid}/chunks/{index:06d}.part", data, "application/octet-stream")
    return {"ok": True, "index": index}


@api.post("/projects/{pid}/upload/complete")
def complete_upload(pid: str, request: Request):
    doc = get_project(pid, request)
    total = (doc["size"] + 5 * 1024 * 1024 - 1) // (5 * 1024 * 1024)
    ext = Path(doc["filename"]).suffix.lower() or ".mp4"
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / f"source{ext}"
        with source.open("wb") as out:
            for i in range(total):
                out.write(blob_bytes(f"clipcut/{pid}/chunks/{i:06d}.part"))
        try:
            info = render_engine.probe(source)
        except Exception as exc:
            execute("UPDATE clipcut_projects SET status='error',error=%s,updated_at=now() WHERE id=%s", (f"Invalid video: {exc}"[:500], pid))
            raise HTTPException(400, "file is not a valid video")
        uploaded = blob_put(f"clipcut/{pid}/source{ext}", source.read_bytes(), "video/mp4")
        thumb_path = None
        try:
            thumb = Path(tmp) / "thumb.jpg"
            render_engine.make_thumbnail(source, thumb, min(1.0, info["duration"] / 2))
            thumb_path = blob_put(f"clipcut/{pid}/thumb.jpg", thumb.read_bytes(), "image/jpeg").pathname
        except Exception:
            pass
        execute("UPDATE clipcut_projects SET status='transcribing',source_pathname=%s,thumbnail_pathname=%s,duration=%s,width=%s,height=%s,updated_at=now() WHERE id=%s", (uploaded.pathname, thumb_path, info["duration"], info["width"], info["height"], pid))
        try:
            payload = transcription.transcribe_video(source)
            execute("UPDATE clipcut_projects SET status='ready',transcript=%s::jsonb,transcript_text=%s,error=NULL,updated_at=now() WHERE id=%s", (json.dumps(payload.get("words") or []), payload.get("text") or "", pid))
        except Exception as exc:
            logging.exception("transcription failed")
            execute("UPDATE clipcut_projects SET status='error',error=%s,updated_at=now() WHERE id=%s", (str(exc)[:500], pid))
    return {"ok": True, "status": "ready", "duration": info["duration"]}


@api.get("/projects")
def list_projects(request: Request, limit: int = 30):
    raw = request.headers.get("x-project-tokens", "")
    hashes = [token_hash(x) for x in raw.split(",") if len(x) >= 32][:100]
    if not hashes:
        return {"projects": []}
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM clipcut_projects WHERE access_token_hash=ANY(%s) ORDER BY created_at DESC LIMIT %s", (hashes, max(1, min(100, limit))))
        docs = cur.fetchall()
    items = []
    for doc in docs:
        export_status = (doc.get("export_state") or {}).get("status", "idle")
        has_thumb = bool(doc.get("thumbnail_pathname"))
        items.append({**normalize(doc), "export_status": export_status, "has_thumb": has_thumb})
    return {"projects": items}


@api.get("/projects/{pid}")
def get_project_route(pid: str, request: Request):
    doc = get_project(pid, request)
    if doc["status"] == "ready":
        doc["cuts"] = compute_cut_state(doc)
    return doc


@api.delete("/projects/{pid}")
def delete_project(pid: str, request: Request):
    doc = get_project(pid, request)
    paths = [doc.get("source_pathname"), doc.get("thumbnail_pathname"), doc.get("export_pathname")]
    blob.delete([p for p in paths if p])
    execute("DELETE FROM clipcut_projects WHERE id=%s AND access_token_hash=%s", (pid, token_hash(project_token(request))))
    return {"ok": True}


class CutSettings(BaseModel):
    pause_threshold: float = .8
    remove_fillers: bool = True
    disabled: list[str] = []


@api.post("/projects/{pid}/cuts")
def update_cuts(pid: str, body: CutSettings, request: Request):
    doc = get_project(pid, request)
    settings = {"pause_threshold": max(.3, min(3, body.pause_threshold)), "remove_fillers": body.remove_fillers, "disabled": body.disabled}
    execute("UPDATE clipcut_projects SET cut_settings=%s::jsonb,updated_at=now() WHERE id=%s", (json.dumps(settings), pid))
    doc["cut_settings"] = settings
    return compute_cut_state(doc)


class StyleBody(BaseModel):
    caption_style: str


@api.post("/projects/{pid}/style")
def set_style(pid: str, body: StyleBody, request: Request):
    get_project(pid, request)
    if body.caption_style not in render_engine.CAPTION_STYLES:
        raise HTTPException(400, "unknown style")
    execute("UPDATE clipcut_projects SET caption_style=%s,updated_at=now() WHERE id=%s", (body.caption_style, pid))
    return {"ok": True}


class ExportBody(BaseModel):
    caption_style: str = "bold"
    burn_captions: bool = True
    aspect: str = "original"
    cinematic: bool = True
    karaoke: bool = True
    zoom_intensity: float = 1
    punch_ins: bool = True
    punch_sensitivity: float = .5


@api.post("/projects/{pid}/export")
def start_export(pid: str, body: ExportBody, request: Request):
    doc = get_project(pid, request)
    if doc["status"] != "ready":
        raise HTTPException(400, "transcript not ready")
    if body.caption_style not in render_engine.CAPTION_STYLES or body.aspect not in ("9:16", "original"):
        raise HTTPException(400, "invalid export settings")
    reel = {"aspect": body.aspect, "cinematic": body.cinematic, "karaoke": body.karaoke, "zoom_intensity": max(.2, min(1.6, body.zoom_intensity)), "punch_ins": body.punch_ins, "punch_sensitivity": max(0, min(1, body.punch_sensitivity)), "burn_captions": body.burn_captions}
    execute("UPDATE clipcut_projects SET caption_style=%s,reel_settings=%s::jsonb,export_state=%s::jsonb,updated_at=now() WHERE id=%s", (body.caption_style, json.dumps(reel), json.dumps({"status":"processing","progress":0,"stage":"cutting"}), pid))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            source.write_bytes(blob_bytes(doc["source_pathname"]))
            out = root / "export.mp4"
            state = compute_cut_state({**doc, "reel_settings": reel})
            def progress(value):
                stage = "cutting" if value < 68 else ("captioning" if value < 90 else "mastering")
                execute("UPDATE clipcut_projects SET export_state=jsonb_set(jsonb_set(export_state,'{progress}',to_jsonb(%s::int)),'{stage}',to_jsonb(%s::text)),updated_at=now() WHERE id=%s", (value, stage, pid))
            meta = render_engine.render_export(source, doc["words"], state["keep_ranges"], body.caption_style, body.burn_captions, root / "work", out, reel["aspect"], reel["cinematic"], reel["karaoke"], reel["zoom_intensity"], reel["punch_ins"], reel["punch_sensitivity"], progress)
            uploaded = blob_put(f"clipcut/{pid}/export-{uuid.uuid4().hex[:8]}.mp4", out.read_bytes(), "video/mp4")
            export = {"status":"done","progress":100,"stage":"done","error":None,"meta":meta,"size":out.stat().st_size,"finished_at":now_iso()}
            execute("UPDATE clipcut_projects SET export_pathname=%s,export_state=%s::jsonb,updated_at=now() WHERE id=%s", (uploaded.pathname, json.dumps(export), pid))
    except Exception as exc:
        logging.exception("export failed")
        execute("UPDATE clipcut_projects SET export_state=%s::jsonb,updated_at=now() WHERE id=%s", (json.dumps({"status":"error","progress":0,"stage":"failed","error":str(exc)[:500]}), pid))
        raise HTTPException(500, str(exc)[:300])
    return {"ok": True, "reel_settings": reel}


@api.get("/projects/{pid}/video")
def source_video(pid: str, request: Request):
    doc = get_project(pid, request)
    return media_response(blob_bytes(doc["source_pathname"]))


@api.get("/projects/{pid}/thumbnail")
def thumbnail(pid: str, request: Request):
    doc = get_project(pid, request)
    return media_response(blob_bytes(doc["thumbnail_pathname"]), "image/jpeg")


@api.get("/projects/{pid}/export/video")
def export_video(pid: str, request: Request):
    doc = get_project(pid, request)
    if not doc.get("export_pathname"):
        raise HTTPException(404, "export not ready")
    return media_response(blob_bytes(doc["export_pathname"]))


@api.get("/projects/{pid}/export/download")
def download(pid: str, request: Request):
    doc = get_project(pid, request)
    if not doc.get("export_pathname"):
        raise HTTPException(404, "export not ready")
    return media_response(blob_bytes(doc["export_pathname"]), filename=f"{Path(doc['filename']).stem}_reel.mp4")


@api.get("/styles")
def styles():
    return {"styles": list(render_engine.CAPTION_STYLES)}


@api.get("/storage/status")
def storage_status():
    return {"blob": True, "database": True, "private": True}


@api.get("/")
def health():
    return {"status": "ok", "service": "clipcut-cloud"}

app.include_router(api)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
