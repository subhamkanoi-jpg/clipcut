import re
from pathlib import Path

PUNCT_BREAK = set(".,!?;:")

# A fonts-only subdirectory: ffmpeg's `subtitles` filter `fontsdir` option
# makes libass try to load every file in the directory it points at as a
# font candidate, so `assets/fonts/README.md` must NOT live in here (it
# stays one level up, at `assets/fonts/README.md`) or libass logs
# "Error opening memory font 'README.md'" on every burn.
FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "ttf"
FONT_FILE = FONTS_DIR / "ClipCutSans-Bold.ttf"
FONT = "DejaVu Sans"           # the family name inside the bundled TTF
FONT_FALLBACK = "Liberation Sans"


def font_available() -> bool:
    return FONT_FILE.is_file()


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
              width: int, height: int, karaoke: bool = True,
              fonts_dir: Path | None = None) -> int:
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
