"""EDL v2: the reviewable edit plan.

Extends the video-use EDL v1 (SKILL.md) with reframe, captions, first-class
audio_overlays, and per-overlay enabled/locked/provenance so the plan can be
reviewed and partially regenerated.
"""

import uuid

PLAN_VERSION = 2
VALID_ASPECTS = ("9:16", "original")
VALID_OVERLAY_KINDS = ("broll", "graphic", "still")


def new_plan(project_id: str, source_path: str) -> dict:
    return {
        "version": PLAN_VERSION,
        "project_id": project_id,
        "sources": {"main": str(source_path)},
        "ranges": [],
        "reframe": {"aspect": "9:16", "center_x": 0.5},
        "captions": {"style": "bold", "karaoke": True, "burn": True},
        "overlays": [],
        "audio_overlays": [],
        "grade": "none",
        "total_duration_s": 0.0,
        "provider": None,
    }


def overlay(kind: str, start_in_output: float, duration: float, **extra) -> dict:
    item = {
        "id": f"ov_{uuid.uuid4().hex[:8]}",
        "kind": kind,
        "start_in_output": float(start_in_output),
        "duration": float(duration),
        "file": None,
        "enabled": True,
        "locked": False,
    }
    item.update(extra)
    return item


def validate(plan: dict) -> list:
    """Return a list of human-readable errors. Empty means valid."""
    errors = []

    if plan.get("version") not in (1, PLAN_VERSION):
        errors.append(f"version must be 1 or {PLAN_VERSION}, got {plan.get('version')!r}")

    sources = plan.get("sources")
    if not isinstance(sources, dict) or not sources:
        errors.append("sources must be a non-empty object")
        return errors

    ranges = plan.get("ranges")
    if not isinstance(ranges, list) or not ranges:
        errors.append("ranges must be a non-empty array")
        return errors

    for i, r in enumerate(ranges):
        if not isinstance(r, dict):
            errors.append(f"ranges[{i}] must be an object")
            continue
        if r.get("source") not in sources:
            errors.append(f"ranges[{i}].source {r.get('source')!r} is not in sources")
            continue
        try:
            start = float(r["start"])
            end = float(r["end"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"ranges[{i}] needs numeric start and end")
            continue
        if start < 0 or end <= start:
            errors.append(f"ranges[{i}] requires 0 <= start < end (got {start}, {end})")

        # `move` (speech-driven camera move: a z0->z1 zoom ramp + optional
        # per-word snap accents, see backend/zooms.py::plan) is optional —
        # older plans and ranges with cinematic zoom disabled simply omit it.
        # When present, only check it's well-formed enough for
        # helpers.render.extract_segment to consume; never reject a plan for
        # lacking it.
        move = r.get("move")
        if move is not None:
            if not isinstance(move, dict):
                errors.append(f"ranges[{i}].move must be an object")
            else:
                for key in ("z0", "z1"):
                    if key in move:
                        try:
                            float(move[key])
                        except (TypeError, ValueError):
                            errors.append(f"ranges[{i}].move.{key} must be numeric")
                snaps = move.get("snaps")
                if snaps is not None:
                    if not isinstance(snaps, list):
                        errors.append(f"ranges[{i}].move.snaps must be an array")
                    else:
                        for j, snap in enumerate(snaps):
                            if not isinstance(snap, dict):
                                errors.append(f"ranges[{i}].move.snaps[{j}] must be an object")
                                continue
                            for key in ("t", "amp"):
                                if key in snap:
                                    try:
                                        float(snap[key])
                                    except (TypeError, ValueError):
                                        errors.append(
                                            f"ranges[{i}].move.snaps[{j}].{key} must be numeric"
                                        )

    reframe = plan.get("reframe") or {}
    if reframe.get("aspect") not in VALID_ASPECTS:
        errors.append(f"reframe.aspect must be one of {VALID_ASPECTS}")
    cx = reframe.get("center_x", 0.5)
    if isinstance(cx, list):
        for j, kf in enumerate(cx):
            if not isinstance(kf, dict):
                errors.append(f"reframe.center_x[{j}] must be an object")
                continue
            try:
                t = float(kf["t"])
                c = float(kf["cx"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"reframe.center_x[{j}] needs numeric t and cx")
                continue
            if t < 0:
                errors.append(f"reframe.center_x[{j}].t must be >= 0")
            if not 0.0 <= c <= 1.0:
                errors.append(f"reframe.center_x[{j}].cx must be in [0, 1], got {c}")
    else:
        try:
            if not 0.0 <= float(cx) <= 1.0:
                errors.append(f"reframe.center_x must be in [0, 1], got {cx}")
        except (TypeError, ValueError):
            errors.append(f"reframe.center_x must be numeric, got {cx!r}")

    for i, ov in enumerate(plan.get("overlays") or []):
        if ov.get("kind") not in VALID_OVERLAY_KINDS:
            errors.append(f"overlays[{i}].kind must be one of {VALID_OVERLAY_KINDS}")
        try:
            if float(ov["duration"]) <= 0:
                errors.append(f"overlays[{i}].duration must be > 0")
        except (KeyError, TypeError, ValueError):
            errors.append(f"overlays[{i}] needs a numeric duration")
        try:
            if float(ov["start_in_output"]) < 0:
                errors.append(f"overlays[{i}].start_in_output must be >= 0")
        except (KeyError, TypeError, ValueError):
            errors.append(f"overlays[{i}] needs a numeric start_in_output")

    return errors
