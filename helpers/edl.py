"""EDL validation and subtitle helpers used by render.py and the local app."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    edl: dict = field(default_factory=dict)


def _resolve(maybe: str, edit_dir: Path) -> Path:
    p = Path(maybe)
    return p if p.is_absolute() else (edit_dir / p).resolve()


def validate_edl(edl: dict, *, edit_dir: Path) -> ValidationResult:
    out = copy.deepcopy(edl)
    errors: list[str] = []
    warnings: list[str] = []
    sources = out.get("sources")
    ranges = out.get("ranges")
    if not isinstance(sources, dict) or not sources:
        errors.append("sources must be a non-empty object")
        return ValidationResult(False, errors, warnings, out)
    if not isinstance(ranges, list) or not ranges:
        errors.append("ranges must be a non-empty array")
        return ValidationResult(False, errors, warnings, out)

    total = 0.0
    for i, r in enumerate(ranges):
        if not isinstance(r, dict):
            errors.append(f"ranges[{i}] must be an object")
            continue
        name = r.get("source")
        if name not in sources:
            errors.append(f"ranges[{i}].source {name!r} is not in sources")
            continue
        path = _resolve(str(sources[name]), edit_dir)
        if not path.exists():
            errors.append(f"source {name!r} file missing: {path}")
        try:
            start = float(r["start"])
            end = float(r["end"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"ranges[{i}] needs numeric start and end")
            continue
        if start < 0 or end <= start:
            errors.append(f"ranges[{i}] requires 0 <= start < end (got {start}, {end})")
            continue
        total += end - start

    overlays = out.get("overlays") or []
    if overlays:
        if not isinstance(overlays, list):
            errors.append("overlays must be an array")
        else:
            for i, ov in enumerate(overlays):
                if not isinstance(ov, dict) or "file" not in ov:
                    errors.append(f"overlays[{i}] needs a file")
                    continue
                op = _resolve(str(ov["file"]), edit_dir)
                if not op.exists():
                    errors.append(f"overlay file missing: {op}")

    stated = out.get("total_duration_s")
    try:
        stated_f = float(stated) if stated is not None else None
    except (TypeError, ValueError):
        stated_f = None
    if stated_f is None or abs(stated_f - total) > 0.05:
        warnings.append(f"total_duration_s corrected to {total:.3f}")
        out["total_duration_s"] = round(total, 3)

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, edl=out)


def escape_subtitles_path(path: Path) -> str:
    """Escape a Windows path for ffmpeg subtitles= filter (drive colon + specials)."""
    s = str(path.resolve()).replace("\\", "/")
    s = s.replace("\\", "/")
    s = s.replace(":", r"\:")
    s = s.replace("'", r"\'")
    s = s.replace("[", r"\[")
    s = s.replace("]", r"\]")
    return s


def default_subtitle_font() -> str:
    """The FontName to request in the burned-caption force_style.

    Only ever names the bundled family (`captions_ass.FONT`) when the
    bundled TTF is actually present on disk (`captions_ass.font_available()`)
    -- libass silently substitutes a missing font rather than erroring, so
    requesting a family we know isn't there would just reintroduce that
    silent-substitution failure mode. Falls back to `captions_ass.FONT_FALLBACK`
    (a family present on the target OSes) when the bundled file is missing,
    and to a hardcoded "Liberation Sans" if `captions_ass` can't even be
    imported.
    """
    try:
        import captions_ass
        return captions_ass.FONT if captions_ass.font_available() else captions_ass.FONT_FALLBACK
    except Exception:
        return "Liberation Sans"


def default_subtitle_fontsdir() -> Path | None:
    """Directory ffmpeg's `subtitles` filter should search for the bundled
    caption font, or None when the bundled TTF is not actually present.

    Callers must check for None and omit the `fontsdir` filter option
    entirely rather than pointing it at a directory with no matching font
    (or one that doesn't exist) -- an unescaped/missing fontsdir value can
    break filter parsing, and a fontsdir with nothing useful in it is
    pointless.
    """
    try:
        import captions_ass
        if captions_ass.font_available():
            return captions_ass.FONTS_DIR
    except Exception:
        pass
    return None


def force_style(*, font: str | None = None, extra: str | None = None) -> str:
    name = font or default_subtitle_font()
    if extra:
        # EDL subtitle_style may be a full force_style or just a font name
        if "FontName=" in extra:
            return extra
        name = extra
    return (
        f"FontName={name},FontSize=18,Bold=1,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
        "BorderStyle=1,Outline=2,Shadow=0,"
        "Alignment=2,MarginV=90"
    )
