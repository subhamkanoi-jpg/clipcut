import json
import re
import subprocess
from pathlib import Path

import zooms

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
TONEMAP_CHAIN = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)
PUNCT_BREAK = set(".,!?;:")
PRESCALE = 1.3
FPS = 30
FONT = "Liberation Sans"

# ASS colours are &HAABBGGRR (style lines) / &HBBGGRR& (inline overrides)
YELLOW = "&H00FFD4&"
WHITE = "&HFFFFFF&"

CAPTION_STYLES = {
    "bold": {
        "uppercase": True, "font": FONT, "size_ratio": 0.055, "bold": -1,
        "primary": "&H00FFFFFF", "outline": "&H00000000", "back": "&H90000000",
        "border_style": 1, "outline_w": 3.4, "shadow": 1.0, "margin_ratio": 0.15,
        "base_inline": WHITE, "hl_inline": YELLOW,
    },
    "neon": {
        "uppercase": True, "font": FONT, "size_ratio": 0.055, "bold": -1,
        "primary": "&H0000FFD4", "outline": "&H00000000", "back": "&H90000000",
        "border_style": 1, "outline_w": 3.4, "shadow": 1.0, "margin_ratio": 0.15,
        "base_inline": WHITE, "hl_inline": YELLOW,
    },
    "boxed": {
        "uppercase": True, "font": FONT, "size_ratio": 0.05, "bold": -1,
        "primary": "&H00FFFFFF", "outline": "&H00000000", "back": "&H00000000",
        "border_style": 3, "outline_w": 5.0, "shadow": 0.0, "margin_ratio": 0.15,
        "base_inline": WHITE, "hl_inline": YELLOW,
    },
    "minimal": {
        "uppercase": False, "font": FONT, "size_ratio": 0.042, "bold": 0,
        "primary": "&H00FFFFFF", "outline": "&H00000000", "back": "&H90000000",
        "border_style": 1, "outline_w": 2.2, "shadow": 0.8, "margin_ratio": 0.13,
        "base_inline": WHITE, "hl_inline": YELLOW,
    },
}


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


# ---------- geometry ----------

def target_size(info: dict, aspect: str) -> tuple:
    if aspect == "9:16":
        return 1080, 1920
    w, h = info["width"] or 1920, info["height"] or 1080
    if w >= h:
        W = min(1920, w)
        return _even(W), _even(h * W / w)
    H = min(1920, h)
    return _even(w * H / h), _even(H)


def crop_spec(info: dict, aspect: str, center_x: float) -> tuple | None:
    """Return (cw, ch, cx, cy) to reach the target aspect, or None."""
    if aspect != "9:16":
        return None
    w, h = info["width"], info["height"]
    if not w or not h:
        return None
    src_ar = w / h
    tgt_ar = 9 / 16
    if abs(src_ar - tgt_ar) < 0.01:
        return None
    if src_ar > tgt_ar:
        cw = _even(h * tgt_ar)
        ch = _even(h)
        cx = int(min(max(0, center_x * w - cw / 2), max(0, w - cw)))
        return cw, min(ch, h), cx, 0
    ch = _even(w / tgt_ar)
    ch = min(ch, h)
    cy = int(max(0, (h - ch) * 0.25))
    return _even(w), ch, 0, cy


# ---------- segment render ----------

def extract_segment(source: Path, start: float, end: float, out_path: Path, info: dict,
                    target: tuple, crop: tuple | None = None, move: dict | None = None) -> None:
    duration = end - start
    W, H = target
    parts = []
    if info["hdr"]:
        parts.append(TONEMAP_CHAIN)
    if crop:
        cw, ch, cx, cy = crop
        parts.append(f"crop={cw}:{ch}:{cx}:{cy}")
    parts.append(f"fps={FPS}")

    zooming = move and (move["z0"] > 1.001 or move["z1"] > 1.001 or move.get("snaps"))
    if zooming:
        pw, ph = _even(W * PRESCALE), _even(H * PRESCALE)
        parts.append(f"scale={pw}:{ph}:flags=bicubic")
        frames = max(1, int(duration * FPS))
        z0, z1 = move["z0"], move["z1"]
        dz = z1 - z0
        terms = [f"{z0:.4f}+({dz:.4f})*on/{frames}"]
        for snap in move.get("snaps") or []:
            f0 = int(snap["t"] * FPS)
            f1 = f0 + max(2, int(snap.get("decay", 0.45) * FPS))
            if f0 >= frames:
                continue
            terms.append(
                f"if(between(on,{f0},{f1}),{snap['amp']:.4f}*(1-(on-{f0})/{f1 - f0}),0)"
            )
        zexpr = f"min({PRESCALE - 0.03:.3f},max(1,{'+'.join(terms)}))"
        parts.append(
            f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={W}x{H}:fps={FPS}"
        )
    else:
        parts.append(f"scale={W}:{H}:flags=bicubic")
    parts.append("setsar=1")

    fade_out = max(0.0, duration - 0.03)
    af = f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out:.3f}:d=0.03"
    _run([
        "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
        "-vf", ",".join(parts), "-af", af,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(out_path),
    ])


def concat_segments(paths: list, out_path: Path, work_dir: Path) -> None:
    concat_list = work_dir / "_concat.txt"
    concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in paths))
    _run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", "-movflags", "+faststart", str(out_path),
    ])
    concat_list.unlink(missing_ok=True)


# ---------- captions (ASS, word-by-word karaoke) ----------

def _ass_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6_000)
    s, c = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{c:02d}"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("{", "(").replace("}", ")")).strip()


def timeline_chunks(words: list, ranges: list, max_words: int = 3) -> list:
    """Map source-time words onto the post-cut output timeline, grouped into chunks."""
    items = [w for w in words if w.get("type") == "word" and w.get("start") is not None]
    out = []
    offset = 0.0
    for r_start, r_end in ranges:
        in_range = [w for w in items if w["end"] > r_start and w["start"] < r_end]
        group, current = [], []
        for w in in_range:
            if not _clean(w.get("text")):
                continue
            current.append(w)
            txt = (w.get("text") or "").strip()
            if len(current) >= max_words or (txt and txt[-1] in PUNCT_BREAK):
                group.append(current)
                current = []
        if current:
            group.append(current)
        for chunk in group:
            mapped = []
            for w in chunk:
                a = max(r_start, w["start"]) - r_start + offset
                b = min(r_end, w["end"]) - r_start + offset
                mapped.append({"text": _clean(w.get("text")), "start": a, "end": max(b, a + 0.05)})
            mapped[-1]["text"] = mapped[-1]["text"].rstrip(",;:")
            out.append({
                "start": mapped[0]["start"],
                "end": max(mapped[-1]["end"], mapped[0]["start"] + 0.35),
                "words": mapped,
            })
        offset += r_end - r_start
    out.sort(key=lambda c: c["start"])
    return out


def build_ass(words: list, ranges: list, out_path: Path, style: dict,
              width: int, height: int, karaoke: bool = True) -> int:
    chunks = timeline_chunks(words, ranges)
    if not chunks:
        out_path.write_text("")
        return 0

    scale = height / 1080
    font_size = max(18, int(height * style["size_ratio"]))
    margin_v = int(height * style["margin_ratio"])
    outline = round(style["outline_w"] * scale, 1)
    shadow = round(style["shadow"] * scale, 1)
    side = int(width * 0.08)

    header = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{style['font']},{font_size},{style['primary']},{style['primary']},"
        f"{style['outline']},{style['back']},{style['bold']},0,0,0,100,100,0,0,"
        f"{style['border_style']},{outline},{shadow},2,{side},{side},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])

    def render(chunk, active_idx):
        pieces = []
        for i, w in enumerate(chunk["words"]):
            t = w["text"].upper() if style["uppercase"] else w["text"]
            if karaoke and i == active_idx:
                pieces.append(
                    f"{{\\c{style['hl_inline']}\\fscx108\\fscy108}}{t}"
                    f"{{\\c{style['base_inline']}\\fscx100\\fscy100}}"
                )
            else:
                pieces.append(t)
        return " ".join(pieces)

    events = []
    for chunk in chunks:
        if karaoke:
            ws = chunk["words"]
            for i, w in enumerate(ws):
                a = chunk["start"] if i == 0 else w["start"]
                b = ws[i + 1]["start"] if i + 1 < len(ws) else chunk["end"]
                if b <= a:
                    b = a + 0.08
                events.append((a, b, render(chunk, i)))
        else:
            events.append((chunk["start"], chunk["end"], render(chunk, -1)))

    lines = [
        f"Dialogue: 0,{_ass_ts(a)},{_ass_ts(b)},Cap,,0,0,0,,{txt}"
        for a, b, txt in events if txt.strip()
    ]
    out_path.write_text(header + "\n" + "\n".join(lines) + "\n")
    return len(lines)


def _escape_sub_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def burn_captions(base: Path, subs: Path, out_path: Path) -> None:
    vf = f"ass='{_escape_sub_path(subs)}'"
    _run([
        "ffmpeg", "-y", "-i", str(base), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", str(out_path),
    ])


def loudnorm(input_path: Path, out_path: Path) -> None:
    _run([
        "ffmpeg", "-y", "-i", str(input_path), "-c:v", "copy",
        "-af", "loudnorm=I=-14:TP=-1:LRA=11",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(out_path),
    ])


# ---------- pipeline ----------

def render_export(source: Path, words: list, ranges: list, style_key: str, burn: bool,
                  work_dir: Path, out_path: Path, aspect: str = "original",
                  cinematic: bool = True, karaoke: bool = True,
                  zoom_intensity: float = 1.0, punch_ins: bool = True,
                  punch_sensitivity: float = 0.5,
                  progress_cb=None) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    info = probe(source)
    style = CAPTION_STYLES.get(style_key, CAPTION_STYLES["bold"])
    target = target_size(info, aspect)

    center_x = 0.5
    crop = None
    if aspect == "9:16":
        crop = crop_spec(info, aspect, 0.5)
        if crop and info["width"] > info["height"]:
            # Vercel Functions do not ship OpenCV; use a centered crop fallback.
            crop = crop_spec(info, aspect, center_x)
    if progress_cb:
        progress_cb(6)

    moves = zooms.plan(words, ranges, zoom_intensity, punch_ins, punch_sensitivity) if cinematic else []

    seg_paths = []
    total = max(1, len(ranges))
    for i, (a, b) in enumerate(ranges):
        seg = work_dir / f"seg_{i:03d}.mp4"
        extract_segment(source, a, b, seg, info, target, crop,
                        moves[i] if i < len(moves) else None)
        seg_paths.append(seg)
        if progress_cb:
            progress_cb(int(8 + 58 * (i + 1) / total))

    base = work_dir / "base.mp4"
    concat_segments(seg_paths, base, work_dir)
    if progress_cb:
        progress_cb(70)

    caption_count = 0
    if burn:
        subs = work_dir / "captions.ass"
        caption_count = build_ass(words, ranges, subs, style, target[0], target[1], karaoke)
        if caption_count:
            captioned = work_dir / "captioned.mp4"
            burn_captions(base, subs, captioned)
            base = captioned
    if progress_cb:
        progress_cb(88)

    loudnorm(base, out_path)
    if progress_cb:
        progress_cb(100)

    for p in seg_paths:
        p.unlink(missing_ok=True)

    punches = [
        {"word": s["word"], "t": round(m["start"] + s["t"], 2), "amp": s["amp"]}
        for m in moves for s in (m.get("snaps") or [])
    ]
    punches.sort(key=lambda p: p["t"])

    return {
        "width": target[0],
        "height": target[1],
        "aspect": aspect,
        "moves": moves,
        "punches": punches[:16],
        "punch_count": len(punches),
        "center_x": round(center_x, 3),
        "caption_events": caption_count,
        "karaoke": bool(burn and karaoke),
    }
