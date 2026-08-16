import json
import subprocess
from pathlib import Path

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}


def _run(cmd: list) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace")
        raise RuntimeError(f"ffmpeg failed: {err[-800:]}")


def _even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def probe(video: Path) -> dict:
    out = subprocess.run(
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
