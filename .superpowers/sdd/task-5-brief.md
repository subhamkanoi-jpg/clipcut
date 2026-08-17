### Task 5: EDL v2 model and validation

**Files:**
- Create: `backend/plan/__init__.py`
- Create: `backend/plan/model.py`
- Create: `backend/tests/test_plan_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `plan.model.new_plan(project_id, source_path) -> dict`; `plan.model.validate(plan: dict) -> list[str]` returning error strings (empty list means valid); `plan.model.overlay(kind, start_in_output, duration, **extra) -> dict` factory that assigns an `id`; `plan.model.PLAN_VERSION = 2`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_plan_model.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plan import model


def test_new_plan_has_v2_shape():
    p = model.new_plan("p1", "data/p1/source.mp4")
    assert p["version"] == 2
    assert p["project_id"] == "p1"
    assert p["sources"] == {"main": "data/p1/source.mp4"}
    assert p["ranges"] == []
    assert p["overlays"] == []
    assert p["audio_overlays"] == []
    assert p["reframe"]["aspect"] == "9:16"
    assert p["captions"]["karaoke"] is True


def test_overlay_factory_assigns_unique_ids():
    a = model.overlay("broll", 1.0, 2.0, query="laptop")
    b = model.overlay("broll", 3.0, 2.0, query="desk")
    assert a["id"] != b["id"]
    assert a["enabled"] is True
    assert a["locked"] is False
    assert a["query"] == "laptop"


def test_validate_accepts_minimal_valid_plan():
    p = model.new_plan("p1", "data/p1/source.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    assert model.validate(p) == []


def test_validate_rejects_empty_ranges():
    p = model.new_plan("p1", "s.mp4")
    assert any("ranges" in e for e in model.validate(p))


def test_validate_rejects_unknown_source():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "nope", "start": 0.0, "end": 1.0}]
    assert any("nope" in e for e in model.validate(p))


def test_validate_rejects_inverted_range():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 5.0, "end": 5.0}]
    assert any("start < end" in e for e in model.validate(p))


def test_validate_rejects_negative_overlay_duration():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    p["overlays"] = [model.overlay("broll", 0.5, -1.0)]
    assert any("duration" in e for e in model.validate(p))


def test_validate_rejects_bad_aspect():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    p["reframe"]["aspect"] = "4:3"
    assert any("aspect" in e for e in model.validate(p))


def test_validate_rejects_center_x_out_of_range():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    p["reframe"]["center_x"] = 1.7
    assert any("center_x" in e for e in model.validate(p))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_plan_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plan'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/plan/__init__.py`:

```python
"""EDL v2 plan model, assembly, and materialization."""
```

Create `backend/plan/model.py`:

```python
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

    if plan.get("version") != PLAN_VERSION:
        errors.append(f"version must be {PLAN_VERSION}, got {plan.get('version')!r}")

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

    reframe = plan.get("reframe") or {}
    if reframe.get("aspect") not in VALID_ASPECTS:
        errors.append(f"reframe.aspect must be one of {VALID_ASPECTS}")
    cx = reframe.get("center_x", 0.5)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_plan_model.py -v`
Expected: PASS, 9 passed

- [ ] **Step 5: Commit**

```bash
git add backend/plan backend/tests/test_plan_model.py
git commit -m "feat: add EDL v2 plan model with validation"
```

---

