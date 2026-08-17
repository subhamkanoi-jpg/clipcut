import json
import subprocess
import sys
from pathlib import Path

# helpers/ modules cross-import flatly, so the directory itself goes on the
# path -- same bootstrap as worker.py/server.py. Needed here just to reach
# hidden_proc, so it's done defensively rather than relying on some other
# already-imported module having inserted it first (server.py currently
# imports this module before it inserts helpers/ onto sys.path itself).
HELPERS = Path(__file__).resolve().parent.parent / "helpers"
if str(HELPERS) not in sys.path:
    sys.path.insert(0, str(HELPERS))

from hidden_proc import run as hidden_run

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}


def _run(cmd: list) -> None:
    proc = hidden_run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace")
        raise RuntimeError(f"ffmpeg failed: {err[-800:]}")


def probe(video: Path) -> dict:
    out = hidden_run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,color_transfer:format=duration",
         "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    stream = (data.get("streams") or [{}])[0]
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration": float((data.get("format") or {}).get("duration") or 0),
        "hdr": stream.get("color_transfer") in HDR_TRANSFERS,
    }


def make_thumbnail(video: Path, out_path: Path, at: float = 1.0) -> None:
    _run(["ffmpeg", "-y", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1",
          "-vf", "scale=360:-2", "-q:v", "4", str(out_path)])
