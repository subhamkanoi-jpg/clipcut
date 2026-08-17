"""The DecisionProvider seam and the picks schema.

picks shape (all keys optional, default []):
  cuts:     [ {range_i:int, variation:str, score:float} ]
  visuals:  [ {kind:"broll"|"still", after_i:int, query:str, duration_s:float} ]
  graphics: [ {text:str, start_s:float, duration_s:float} ]

These mirror what helpers/visual_picks and helpers/cut_picks consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

VISUAL_KINDS = ("broll", "still")


@dataclass
class PlanContext:
    edit_dir: Path
    words: list
    text: str
    ranges: list
    total_s: float


@runtime_checkable
class DecisionProvider(Protocol):
    name: str

    def plan(self, ctx: PlanContext) -> dict | None:
        ...


def _num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def validate_picks(data: object) -> list:
    errors: list = []
    if not isinstance(data, dict):
        return ["picks must be an object"]

    cuts = data.get("cuts", [])
    if not isinstance(cuts, list):
        errors.append("cuts must be a list")
    else:
        for i, c in enumerate(cuts):
            if not isinstance(c, dict) or not _num(c.get("range_i")):
                errors.append(f"cuts[{i}] needs a numeric range_i")

    visuals = data.get("visuals", [])
    if not isinstance(visuals, list):
        errors.append("visuals must be a list")
    else:
        for i, v in enumerate(visuals):
            if not isinstance(v, dict):
                errors.append(f"visuals[{i}] must be an object")
                continue
            if v.get("kind") not in VISUAL_KINDS:
                errors.append(f"visuals[{i}].kind must be one of {VISUAL_KINDS}")
            if not str(v.get("query") or "").strip():
                errors.append(f"visuals[{i}] needs a non-empty query")
            if not _num(v.get("after_i")):
                errors.append(f"visuals[{i}] needs a numeric after_i")
            if "duration_s" in v and (not _num(v["duration_s"]) or float(v["duration_s"]) <= 0):
                errors.append(f"visuals[{i}].duration_s must be > 0")

    graphics = data.get("graphics", [])
    if not isinstance(graphics, list):
        errors.append("graphics must be a list")
    else:
        for i, g in enumerate(graphics):
            if not isinstance(g, dict):
                errors.append(f"graphics[{i}] must be an object")
                continue
            if not str(g.get("text") or "").strip():
                errors.append(f"graphics[{i}] needs non-empty text")
            if "duration_s" in g and (not _num(g["duration_s"]) or float(g["duration_s"]) <= 0):
                errors.append(f"graphics[{i}].duration_s must be > 0")

    return errors
