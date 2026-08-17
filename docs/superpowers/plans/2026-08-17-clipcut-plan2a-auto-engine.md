# ClipCut Plan 2a — Auto-Plan Engine + B-roll/Stills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a transcribed ClipCut project into a reviewable edit plan that carries AI-chosen b-roll, stills, and keyword graphics, and render them into the reel — with the whole pipeline working when no LLM is available.

**Architecture:** A pluggable `DecisionProvider` produces `picks` (cut scores, b-roll queries, graphics) from the transcript. A new `plan` job runs the provider, assembles picks into EDL v2 overlays carrying review metadata (`enabled`/`locked`/`query`/`source`) with their media unresolved, and stores the plan on the project document. The render job resolves each enabled overlay's media at render time (fetch b-roll, Ken Burns stills, or a fallback graphic) and composites it. `HeuristicProvider` is both the offline fallback and the LLM-free test double; `ClaudeCliProvider` sits behind the same seam.

**Tech Stack:** Python 3.12, FastAPI, pymongo, the `claude` CLI as a subprocess, ffmpeg/PIL via existing `helpers/`.

This plan implements the decision-provider, `picks.json`, Claude-invocation, and
b-roll/stills portions of
`docs/superpowers/specs/2026-08-17-clipcut-auto-editor-design.md`. The plan-review
UI and the overlay `PATCH` route are deferred to Plan 2b.

## Global Constraints

- Python 3.12. The venv is `.venv-local/` at the repo root; run backend code as `../.venv-local/Scripts/python.exe` from `backend/`, and helpers/root tests as `.venv-local/Scripts/python.exe` from the repo root.
- Backend modules use **flat imports** (`import cuts`); `backend/` is the cwd for backend pytest. `helpers/` goes on `sys.path` as a directory and is never imported as a package. Do not convert either.
- No new system services and no new third-party dependencies. The `claude` CLI is already installed and on PATH (`claude --version` works).
- On Windows, spawn every child process via `helpers/hidden_proc.py:run` (avoids console-window flashes). The `claude` subprocess uses the pattern in `app/server/claude.py`.
- Mongo is the source of truth; `data/<pid>/edit/` is derived and safe to delete.
- Creative intelligence comes from the `claude` CLI as a one-shot subprocess, not the Anthropic API. One structured call that writes `picks.json`; stdout is not parsed. Any failure (binary missing, timeout, invalid JSON) logs and falls back to `HeuristicProvider`. **Planning never hard-fails.**
- The whole pipeline must produce a valid reel with `HeuristicProvider` alone — no LLM in the loop.
- `helpers/` is shared with the standalone `video-use` skill. Do not change the behaviour of any existing `helpers/` function; only call them.
- Tests use the `clipcut_test` database and drop it on teardown. Never point tests at `clipcut`.
- Network-dependent asset fetching (Mixkit/Pexels) is stubbed in tests. A small committed fixture stands in for a fetched clip.
- **Never `git add -A` / `git add .` / `git commit -a`.** Stage explicit paths only; run `git status --short` before committing. `.venv-local/` and `backend/data/` are gitignored — do not commit them.

## Interfaces that already exist (consume, do not rebuild)

- `backend/plan/model.py`: `PLAN_VERSION = 2`, `new_plan(project_id, source_path) -> dict`, `overlay(kind, start_in_output, duration, **extra) -> dict` (assigns `id`, sets `enabled=True`, `locked=False`, `file=None`), `validate(plan) -> list[str]`. Valid overlay kinds: `("broll", "graphic", "still")`.
- `backend/plan/assemble.py`: `from_project(doc, cut_state) -> dict` — builds a v2 plan with ranges/reframe/captions and **empty** overlays.
- `backend/plan/materialize.py`: `edit_dir(project_dir) -> Path`, `write(plan, project_dir) -> Path`, `clean(project_dir)`.
- `backend/plan/render_plan.py`: `render(plan, project_dir, out_path, words, progress_cb=None, cancel_cb=None) -> dict`. Internally `_composite(base, plan, subs_path, work_dir, edit_dir)` filters `overlays` on `enabled` and calls `helpers.render.build_final_composite`.
- `backend/cut_state.py`: `compute_cut_state(doc) -> dict` (has `keep_ranges`, `moves`), `now_iso()`.
- `backend/handlers/` pattern: a module assigns `worker.HANDLERS["kind"] = run`; `worker._register_handlers()` imports it. `Ctx` has `db`, `project_id`, `payload`, `progress(p, stage)`, `cancelled()`. `worker.Cancelled` lives in `backend/errors.py` (re-exported from `worker`).
- `backend/jobs.py`: `enqueue(db, project_id, kind, payload=None) -> str`.
- `helpers/visual_picks.py`: `resolve_broll_file(vis: dict, dest_dir: Path) -> Path | None` (fetches a Mixkit clip for `vis["query"]`, or returns a local file if `vis["file"]` exists), `photo_to_clip(photo: Path, dest: Path, duration: float) -> Path` (Ken Burns a still to 1080x1920), `make_keyword_graphic(text: str, edit_dir: Path, duration: float) -> Path`, `output_time_at(ranges: list[dict], index: int) -> float`, `IMAGE_EXTS`, `MAX_VISUALS = 4`, `MAX_BROLL = 2.6`, `MIN_BROLL = 1.2`.
- `helpers/pexels_library.py`: `match_photo(query, items=None) -> dict | None`, `load_photo_catalog() -> dict`.
- `backend/zooms.py`: `HOOK_WORDS` (a set of emphasis words). This is a **backend** module, not a helper — `import zooms` resolves to it from the backend path.
- `app/server/claude.py`: `_claude_bin() -> str` (resolves the `claude` executable path). Read it for the subprocess-invocation shape.

---

### Task 1: EDL v2 validation accepts the v1-compat shape

**Files:**
- Modify: `backend/plan/model.py` (the `validate` function)
- Test: `backend/tests/test_plan_model.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `validate(plan)` accepts `version` 1 or 2, and `reframe.center_x` as either a scalar in `[0,1]` or a keyframe list `[{"t": float, "cx": float}, ...]`.

This closes the whole-branch review's plan-2 carry-forward: the spec requires
"EDL v2 validation including the v1-compatibility path", and readers must accept
both the scalar `center_x` and a future keyframe track.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_plan_model.py`:

```python
def test_validate_accepts_version_1():
    p = model.new_plan("p1", "s.mp4")
    p["version"] = 1
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    assert model.validate(p) == []


def test_validate_accepts_center_x_keyframe_list():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    p["reframe"]["center_x"] = [{"t": 0.0, "cx": 0.4}, {"t": 1.5, "cx": 0.6}]
    assert model.validate(p) == []


def test_validate_rejects_keyframe_with_bad_cx():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    p["reframe"]["center_x"] = [{"t": 0.0, "cx": 1.9}]
    assert any("center_x" in e for e in model.validate(p))


def test_validate_still_rejects_version_3():
    p = model.new_plan("p1", "s.mp4")
    p["version"] = 3
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    assert any("version" in e for e in model.validate(p))
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_plan_model.py -v`
Expected: the four new tests FAIL (version-1 rejected; keyframe list rejected as non-numeric center_x).

- [ ] **Step 3: Implement**

In `backend/plan/model.py`, replace the version check and the `center_x` check in `validate`:

```python
    if plan.get("version") not in (1, PLAN_VERSION):
        errors.append(f"version must be 1 or {PLAN_VERSION}, got {plan.get('version')!r}")
```

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_plan_model.py -v`
Expected: PASS (all, including the pre-existing ones).

- [ ] **Step 5: Commit**

```bash
git add backend/plan/model.py backend/tests/test_plan_model.py
git commit -m "feat: EDL v2 validation accepts v1 version and center_x keyframes"
```

---

### Task 2: picks schema and the DecisionProvider seam

**Files:**
- Create: `backend/plan/providers/__init__.py`
- Create: `backend/plan/providers/base.py`
- Test: `backend/tests/test_picks.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `PlanContext` dataclass with fields `edit_dir: Path`, `words: list[dict]`, `text: str`, `ranges: list[dict]`, `total_s: float`.
  - `validate_picks(data: object) -> list[str]` — returns error strings; empty list means valid. A valid picks object is a dict whose `cuts`/`visuals`/`graphics` (each optional, defaulting to `[]`) are lists of the shapes in the spec.
  - `DecisionProvider` protocol: attribute `name: str`, method `plan(self, ctx: PlanContext) -> dict | None`. Returns a picks dict, or `None` on failure.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_picks.py`:

```python
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plan.providers import base


def test_empty_object_is_valid():
    assert base.validate_picks({}) == []


def test_full_valid_object():
    picks = {
        "cuts": [{"range_i": 0, "variation": "push", "score": 0.8}],
        "visuals": [{"kind": "broll", "after_i": 2, "query": "typing on laptop", "duration_s": 2.4}],
        "graphics": [{"text": "NEVER", "start_s": 1.1, "duration_s": 1.6}],
    }
    assert base.validate_picks(picks) == []


def test_non_dict_is_invalid():
    assert base.validate_picks([1, 2]) != []


def test_cuts_must_be_a_list():
    assert any("cuts" in e for e in base.validate_picks({"cuts": {}}))


def test_visual_needs_kind_and_query():
    assert any("visuals[0]" in e for e in base.validate_picks({"visuals": [{"after_i": 1}]}))


def test_graphic_needs_text():
    assert any("graphics[0]" in e for e in base.validate_picks({"graphics": [{"start_s": 1.0}]}))


def test_bad_duration_rejected():
    picks = {"visuals": [{"kind": "broll", "query": "x", "after_i": 0, "duration_s": -1}]}
    assert any("duration" in e for e in base.validate_picks(picks))


def test_plan_context_holds_fields(tmp_path):
    ctx = base.PlanContext(edit_dir=tmp_path, words=[], text="hi", ranges=[], total_s=0.0)
    assert ctx.edit_dir == tmp_path
    assert ctx.text == "hi"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_picks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plan.providers'`.

- [ ] **Step 3: Implement**

Create `backend/plan/providers/__init__.py`:

```python
"""Decision providers: transcript -> picks (cut scores, b-roll, graphics)."""
```

Create `backend/plan/providers/base.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_picks.py -v`
Expected: PASS, 8 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/plan/providers/__init__.py backend/plan/providers/base.py backend/tests/test_picks.py
git commit -m "feat: DecisionProvider seam and picks schema validation"
```

---

### Task 3: HeuristicProvider

**Files:**
- Create: `backend/plan/providers/heuristic.py`
- Test: `backend/tests/test_heuristic_provider.py`

**Interfaces:**
- Consumes: `PlanContext` (Task 2); `helpers/zooms.py:HOOK_WORDS`; `helpers/pexels_library.py:match_photo`.
- Produces: `HeuristicProvider` — attribute `name = "heuristic"`, method `plan(ctx) -> dict` that **always** returns a valid picks dict (never `None`). It picks up to `MAX_HEURISTIC_VISUALS` b-roll/still queries from noun-ish transcript words and up to `MAX_HEURISTIC_GRAPHICS` keyword graphics from hook words, and leaves `cuts` empty (zoom cuts already come from `cut_state["moves"]`).

The heuristic is deliberately simple: it exists to keep the whole pipeline working
with no LLM, and to be the test double for every downstream task. It is not
trying to be as good as Claude.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_heuristic_provider.py`:

```python
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

from plan.providers import base
from plan.providers.heuristic import HeuristicProvider


def _ctx(tmp_path, words):
    text = " ".join(w["text"] for w in words)
    ranges = [{"source": "main", "start": 0.0, "end": 6.0}]
    return base.PlanContext(edit_dir=tmp_path, words=words, text=text, ranges=ranges, total_s=6.0)


WORDS = [
    {"text": "This", "start": 0.0, "end": 0.3, "type": "word"},
    {"text": "laptop", "start": 0.4, "end": 0.9, "type": "word"},
    {"text": "changes", "start": 1.0, "end": 1.4, "type": "word"},
    {"text": "everything", "start": 1.5, "end": 2.1, "type": "word"},
    {"text": "about", "start": 2.2, "end": 2.5, "type": "word"},
    {"text": "coding", "start": 2.6, "end": 3.2, "type": "word"},
]


def test_always_returns_valid_picks(tmp_path):
    picks = HeuristicProvider().plan(_ctx(tmp_path, WORDS))
    assert base.validate_picks(picks) == []


def test_produces_at_least_one_visual(tmp_path):
    picks = HeuristicProvider().plan(_ctx(tmp_path, WORDS))
    assert len(picks["visuals"]) >= 1
    v = picks["visuals"][0]
    assert v["kind"] in base.VISUAL_KINDS
    assert v["query"]
    assert 0 <= int(v["after_i"]) < 1  # only one range in this ctx


def test_hook_word_becomes_a_graphic(tmp_path):
    picks = HeuristicProvider().plan(_ctx(tmp_path, WORDS))
    texts = [g["text"].lower() for g in picks["graphics"]]
    assert "everything" in texts  # 'everything' is in zooms.HOOK_WORDS


def test_empty_transcript_is_valid_and_empty(tmp_path):
    picks = HeuristicProvider().plan(_ctx(tmp_path, []))
    assert base.validate_picks(picks) == []
    assert picks["visuals"] == []
    assert picks["graphics"] == []


def test_name_is_heuristic():
    assert HeuristicProvider().name == "heuristic"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_heuristic_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plan.providers.heuristic'`.

- [ ] **Step 3: Implement**

Create `backend/plan/providers/heuristic.py`:

```python
"""LLM-free decision provider: keyword heuristics over the transcript.

Doubles as the fallback when Claude is unavailable and as the test double for
the whole pipeline.
"""

from __future__ import annotations

import re

from plan.providers.base import PlanContext

try:
    from zooms import HOOK_WORDS  # backend/zooms.py (flat import; backend is on sys.path)
except Exception:  # guard for import-order safety only
    HOOK_WORDS = set()

MAX_HEURISTIC_VISUALS = 3
MAX_HEURISTIC_GRAPHICS = 3
BROLL_DURATION_S = 2.2
GRAPHIC_DURATION_S = 1.6

# Words that make poor b-roll subjects even though they are long.
_STOP = {
    "about", "there", "their", "would", "could", "should", "really", "because",
    "something", "everything", "nothing", "anything", "another", "actually",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


class HeuristicProvider:
    name = "heuristic"

    def plan(self, ctx: PlanContext) -> dict:
        words = [w for w in ctx.words if w.get("type", "word") == "word"]
        n_ranges = max(1, len(ctx.ranges))

        visuals: list[dict] = []
        seen_q: set[str] = set()
        for w in words:
            norm = _norm(w.get("text", ""))
            if len(norm) < 5 or norm in _STOP or norm in seen_q:
                continue
            seen_q.add(norm)
            after_i = self._range_of(ctx, float(w.get("start") or 0.0), n_ranges)
            visuals.append({
                "kind": "broll",
                "after_i": after_i,
                "query": norm,
                "duration_s": BROLL_DURATION_S,
            })
            if len(visuals) >= MAX_HEURISTIC_VISUALS:
                break

        graphics: list[dict] = []
        for w in words:
            norm = _norm(w.get("text", ""))
            if norm in HOOK_WORDS:
                graphics.append({
                    "text": w.get("text", "").strip(),
                    "start_s": float(w.get("start") or 0.0),
                    "duration_s": GRAPHIC_DURATION_S,
                })
            if len(graphics) >= MAX_HEURISTIC_GRAPHICS:
                break

        return {"cuts": [], "visuals": visuals, "graphics": graphics}

    @staticmethod
    def _range_of(ctx: PlanContext, t: float, n_ranges: int) -> int:
        """Which kept range does output time t fall in? Clamped to [0, n-1]."""
        elapsed = 0.0
        for i, r in enumerate(ctx.ranges):
            dur = float(r.get("end") or 0) - float(r.get("start") or 0)
            if t < float(r.get("start") or 0) + dur:
                return i
            elapsed += dur
        return n_ranges - 1
```

Note: `test_hook_word_becomes_a_graphic` expects `everything` as a graphic while
`_STOP` excludes it from *b-roll*. Those are two separate lists — `_STOP` gates
visuals only; graphics come from `HOOK_WORDS`. Confirm `everything` is in
`helpers/zooms.py:HOOK_WORDS` before relying on the test (it is, per that file).

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_heuristic_provider.py -v`
Expected: PASS, 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/plan/providers/heuristic.py backend/tests/test_heuristic_provider.py
git commit -m "feat: HeuristicProvider (LLM-free picks from the transcript)"
```

---

### Task 4: Assemble picks into EDL v2 overlays (lock-preserving)

**Files:**
- Create: `backend/plan/overlays.py`
- Test: `backend/tests/test_overlays_assemble.py`

**Interfaces:**
- Consumes: `plan.model.overlay` (Task 1 file); `helpers/visual_picks.py:output_time_at`.
- Produces: `overlays_from_picks(picks: dict, ranges: list[dict], total_s: float, locked: list[dict] | None = None) -> list[dict]` — returns EDL v2 overlays. Each visual becomes an overlay with `kind`, `start_in_output` (computed from `after_i` via `output_time_at`), `duration`, `query`, `source`, `after_i`, `file=None`, `enabled=True`, `locked=False`. Each graphic becomes a `kind="graphic"` overlay with `text` and `start_in_output=start_s`. Any overlay in `locked` is carried through unchanged and its slot is not regenerated.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_overlays_assemble.py`:

```python
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

from plan import overlays as ov_mod
from plan import model

RANGES = [
    {"source": "main", "start": 0.0, "end": 2.0},
    {"source": "main", "start": 5.0, "end": 8.0},
]
PICKS = {
    "cuts": [],
    "visuals": [{"kind": "broll", "after_i": 1, "query": "laptop", "duration_s": 2.4}],
    "graphics": [{"text": "NEVER", "start_s": 0.5, "duration_s": 1.6}],
}


def test_visual_becomes_v2_overlay():
    ovs = ov_mod.overlays_from_picks(PICKS, RANGES, 5.0)
    broll = [o for o in ovs if o["kind"] == "broll"][0]
    assert broll["query"] == "laptop"
    assert broll["source"] == "mixkit"
    assert broll["file"] is None
    assert broll["enabled"] is True
    assert broll["locked"] is False
    assert broll["id"]
    # after_i=1 -> starts at the output time where range index 1 begins = 2.0s
    assert abs(broll["start_in_output"] - 2.0) < 0.01


def test_graphic_becomes_v2_overlay():
    ovs = ov_mod.overlays_from_picks(PICKS, RANGES, 5.0)
    g = [o for o in ovs if o["kind"] == "graphic"][0]
    assert g["text"] == "NEVER"
    assert abs(g["start_in_output"] - 0.5) < 0.01


def test_result_validates_inside_a_plan():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = RANGES
    p["overlays"] = ov_mod.overlays_from_picks(PICKS, RANGES, 5.0)
    assert model.validate(p) == []


def test_locked_overlays_are_preserved_and_not_duplicated():
    first = ov_mod.overlays_from_picks(PICKS, RANGES, 5.0)
    locked = [dict(first[0], locked=True)]
    again = ov_mod.overlays_from_picks(PICKS, RANGES, 5.0, locked=locked)
    # the locked overlay survives verbatim (same id)...
    assert any(o["id"] == locked[0]["id"] and o["locked"] for o in again)
    # ...and fresh overlays are still added for the picks
    assert len(again) >= len(locked)


def test_still_kind_is_carried_through():
    picks = {"visuals": [{"kind": "still", "after_i": 0, "query": "desk", "duration_s": 2.0}]}
    ovs = ov_mod.overlays_from_picks(picks, RANGES, 5.0)
    assert ovs[0]["kind"] == "still"
    assert ovs[0]["source"] == "pexels"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_overlays_assemble.py -v`
Expected: FAIL — `ImportError: cannot import name 'overlays'`.

- [ ] **Step 3: Implement**

Create `backend/plan/overlays.py`:

```python
"""Assemble picks into EDL v2 overlays, and resolve their media at render time.

Planning produces overlays with file=None and full review metadata
(enabled/locked/query/source). Resolution (Task 6) fetches the media just
before compositing.
"""

from __future__ import annotations

from pathlib import Path

from plan import model

# Where each visual kind's media comes from, for the review UI's "swap".
_SOURCE = {"broll": "mixkit", "still": "pexels"}


def overlays_from_picks(picks: dict, ranges: list, total_s: float,
                        locked: list | None = None) -> list:
    """Build EDL v2 overlays from a picks dict, preserving locked overlays."""
    from visual_picks import output_time_at  # helpers, on sys.path

    out: list = list(locked or [])

    for vis in (picks.get("visuals") or []):
        kind = vis.get("kind")
        if kind not in _SOURCE:
            continue
        try:
            after_i = int(vis.get("after_i") or 0)
        except (TypeError, ValueError):
            after_i = 0
        start = output_time_at(ranges, after_i)
        dur = float(vis.get("duration_s") or 2.0)
        if total_s > 0:
            dur = min(dur, max(0.8, total_s - start))
        out.append(model.overlay(
            kind, round(start, 2), round(dur, 2),
            query=str(vis.get("query") or "").strip(),
            source=_SOURCE[kind],
            after_i=after_i,
        ))

    for g in (picks.get("graphics") or []):
        text = str(g.get("text") or "").strip()
        if not text:
            continue
        out.append(model.overlay(
            "graphic",
            round(float(g.get("start_s") or 0.0), 2),
            round(float(g.get("duration_s") or 1.6), 2),
            text=text,
            source="pil",
        ))

    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_overlays_assemble.py -v`
Expected: PASS, 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/plan/overlays.py backend/tests/test_overlays_assemble.py
git commit -m "feat: assemble picks into lock-preserving EDL v2 overlays"
```

---

### Task 5: Resolve overlay media at render time

**Files:**
- Modify: `backend/plan/overlays.py` (add `resolve_overlays`)
- Test: `backend/tests/test_overlays_resolve.py`

**Interfaces:**
- Consumes: `helpers/visual_picks.py:resolve_broll_file`, `photo_to_clip`, `make_keyword_graphic`, `IMAGE_EXTS`; `helpers/pexels_library.py:match_photo`.
- Produces: `resolve_overlays(overlays: list, edit_dir: Path, *, fetch: bool = True) -> list` — returns a new list where each **enabled** overlay has a real local `file`. Resolution by kind: `broll` → `resolve_broll_file`; `still` → `match_photo` then `photo_to_clip` (Ken Burns); `graphic` → `make_keyword_graphic`. Any overlay whose media cannot be fetched falls back to a keyword graphic of its `query`/`text`, so an overlay is never dropped. Disabled overlays are returned untouched (still `file=None`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_overlays_resolve.py`:

```python
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

from plan import overlays as ov_mod
from plan import model


def _graphic_clip(edit_dir, text, dur):
    p = Path(edit_dir) / f"g_{text}.mp4"
    p.write_bytes(b"clip")
    return p


def test_graphic_overlay_is_rendered_to_a_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(ov_mod, "make_keyword_graphic",
                        lambda text, ed, dur: _graphic_clip(ed, text, dur))
    ov = model.overlay("graphic", 0.5, 1.6, text="NEVER", source="pil")
    out = ov_mod.resolve_overlays([ov], tmp_path)
    assert out[0]["file"] and Path(out[0]["file"]).is_file()


def test_broll_uses_resolve_broll_file(tmp_path, monkeypatch):
    fetched = tmp_path / "laptop.mp4"
    fetched.write_bytes(b"video")
    monkeypatch.setattr(ov_mod, "resolve_broll_file", lambda vis, dest: fetched)
    ov = model.overlay("broll", 2.0, 2.4, query="laptop", source="mixkit", after_i=1)
    out = ov_mod.resolve_overlays([ov], tmp_path)
    assert Path(out[0]["file"]) == fetched.resolve()


def test_failed_broll_falls_back_to_graphic(tmp_path, monkeypatch):
    monkeypatch.setattr(ov_mod, "resolve_broll_file", lambda vis, dest: None)
    monkeypatch.setattr(ov_mod, "make_keyword_graphic",
                        lambda text, ed, dur: _graphic_clip(ed, text, dur))
    ov = model.overlay("broll", 2.0, 2.4, query="laptop", source="mixkit", after_i=1)
    out = ov_mod.resolve_overlays([ov], tmp_path)
    assert out[0]["file"] and Path(out[0]["file"]).is_file()


def test_disabled_overlay_is_not_resolved(tmp_path):
    ov = model.overlay("broll", 2.0, 2.4, query="laptop", source="mixkit", after_i=1)
    ov["enabled"] = False
    out = ov_mod.resolve_overlays([ov], tmp_path)
    assert out[0]["file"] is None


def test_still_matches_photo_then_ken_burns(tmp_path, monkeypatch):
    photo = tmp_path / "desk.jpg"
    photo.write_bytes(b"jpeg")
    clip = tmp_path / "desk.mp4"
    monkeypatch.setattr(ov_mod, "match_photo", lambda q, items=None: {"file": str(photo)})
    monkeypatch.setattr(ov_mod, "photo_to_clip", lambda p, dest, dur: (dest.write_bytes(b"clip") or dest))
    ov = model.overlay("still", 1.0, 2.0, query="desk", source="pexels", after_i=0)
    out = ov_mod.resolve_overlays([ov], tmp_path)
    assert out[0]["file"] and Path(out[0]["file"]).is_file()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_overlays_resolve.py -v`
Expected: FAIL — `AttributeError: module 'plan.overlays' has no attribute 'resolve_overlays'` (or the monkeypatch targets don't exist yet).

- [ ] **Step 3: Implement**

Add module-level imports and `resolve_overlays` to `backend/plan/overlays.py`. Put the helper imports at module scope so the tests can monkeypatch them on `ov_mod`:

```python
# --- render-time resolution ---
# Imported at module scope so tests can monkeypatch them on this module.
try:
    from visual_picks import (
        resolve_broll_file, photo_to_clip, make_keyword_graphic, IMAGE_EXTS,
    )
    from pexels_library import match_photo
except Exception:  # helpers not importable in some unit contexts
    resolve_broll_file = photo_to_clip = make_keyword_graphic = match_photo = None
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _broll_dir(edit_dir: Path) -> Path:
    d = Path(edit_dir) / "bin" / "broll"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_overlays(overlays: list, edit_dir: Path, *, fetch: bool = True) -> list:
    """Return overlays with each enabled one's media resolved to a local file."""
    edit_dir = Path(edit_dir)
    out: list = []
    for ov in overlays:
        item = dict(ov)
        if not item.get("enabled", True):
            out.append(item)
            continue
        if item.get("file") and Path(item["file"]).is_file():
            out.append(item)
            continue

        kind = item.get("kind")
        dur = float(item.get("duration") or 2.0)
        resolved: Path | None = None

        if kind == "graphic":
            resolved = make_keyword_graphic(str(item.get("text") or "NOW"), edit_dir, dur)
        elif kind == "broll" and fetch:
            resolved = resolve_broll_file({"query": item.get("query")}, _broll_dir(edit_dir))
        elif kind == "still" and fetch:
            photo = match_photo(str(item.get("query") or ""))
            src = Path(str(photo.get("file"))) if photo and photo.get("file") else None
            if src and src.is_file():
                clip = _broll_dir(edit_dir) / f"{src.stem}.mp4"
                resolved = photo_to_clip(src, clip, dur)

        if resolved is None or not Path(resolved).is_file():
            # Never drop an overlay: fall back to a keyword graphic.
            label = str(item.get("query") or item.get("text") or "B-ROLL")
            resolved = make_keyword_graphic(label.upper()[:18], edit_dir, dur)

        item["file"] = str(Path(resolved).resolve())
        out.append(item)
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_overlays_resolve.py -v`
Expected: PASS, 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/plan/overlays.py backend/tests/test_overlays_resolve.py
git commit -m "feat: resolve overlay media at render time with graphic fallback"
```

---

### Task 6: Wire overlay resolution into the render path

**Files:**
- Modify: `backend/plan/render_plan.py` (`_composite` resolves before compositing)
- Test: `backend/tests/test_render_plan.py`

**Interfaces:**
- Consumes: `plan.overlays.resolve_overlays` (Task 5).
- Produces: `render_plan.render` resolves the plan's enabled overlays (fetching their media) immediately before compositing, so overlays with `file=None` still appear in the output.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_render_plan.py`:

```python
def test_composite_resolves_overlays_before_compositing(tmp_path, monkeypatch):
    from plan import overlays as ov_mod
    from plan import model, render_plan

    seen = {}

    def fake_resolve(ovs, edit_dir, fetch=True):
        seen["called"] = True
        return [dict(o, file=str(tmp_path / "clip.mp4")) for o in ovs]

    def fake_build(base, overlays, subs, out, edit_dir):
        seen["overlay_files"] = [o.get("file") for o in overlays]
        out.write_bytes(b"x")

    monkeypatch.setattr(ov_mod, "resolve_overlays", fake_resolve)
    monkeypatch.setattr(render_plan.helpers_render, "build_final_composite", fake_build)

    (tmp_path / "base.mp4").write_bytes(b"base")
    p = model.new_plan("p1", str(tmp_path / "source.mp4"))
    p["overlays"] = [model.overlay("broll", 1.0, 2.0, query="x", source="mixkit", after_i=0)]
    render_plan._composite(tmp_path / "base.mp4", p, None, tmp_path, tmp_path)

    assert seen.get("called") is True
    assert seen["overlay_files"] == [str(tmp_path / "clip.mp4")]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_render_plan.py::test_composite_resolves_overlays_before_compositing -v`
Expected: FAIL — `resolve_overlays` is never called; `overlay_files` contains `None`.

- [ ] **Step 3: Implement**

In `backend/plan/render_plan.py`, add the import near the top:

```python
from plan import overlays as ov_mod
```

and change `_composite` so it resolves before filtering to `build_final_composite`:

```python
def _composite(base, plan, subs_path, work_dir, edit_dir):
    out = work_dir / "composite.mp4"
    enabled = [o for o in (plan.get("overlays") or []) if o.get("enabled")]
    resolved = ov_mod.resolve_overlays(enabled, edit_dir) if enabled else []
    helpers_render.build_final_composite(base, resolved, subs_path, out, edit_dir)
    return out
```

(If `_composite`'s existing body differs, keep its structure and only insert the
`resolve_overlays` call between the `enabled` filter and `build_final_composite`.)

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_render_plan.py -v`
Expected: PASS (the new test and all existing render_plan tests).

- [ ] **Step 5: Commit**

```bash
git add backend/plan/render_plan.py backend/tests/test_render_plan.py
git commit -m "feat: resolve overlay media inside the render composite step"
```

---

### Task 7: The `plan` job — provider → overlays → project doc

**Files:**
- Create: `backend/handlers/plan.py`
- Modify: `backend/worker.py` (`_register_handlers` imports it)
- Test: `backend/tests/test_handler_plan.py`

**Interfaces:**
- Consumes: `Ctx`/`worker.HANDLERS`; `cut_state.compute_cut_state`; `plan.assemble.from_project`; `plan.materialize.write`; `plan.overlays.overlays_from_picks`; `plan.providers.heuristic.HeuristicProvider`; `plan.providers.base.PlanContext`.
- Produces: `handlers.plan.run(ctx) -> dict` under kind `"plan"`. Reads the project doc, computes cuts, assembles the base EDL, runs the provider chain (Task 8 adds Claude ahead of heuristic; for now heuristic only), assembles overlays (preserving any `locked` overlays from the project's existing plan when `payload.regenerate` is true), stores the full plan under `project["plan"]` with `project["plan_provider"]` set to the provider name, and sets `project["plan_status"] = "ready"`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_handler_plan.py`:

```python
import os
import sys
from pathlib import Path

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

import jobs as jobs_mod
import worker as worker_mod
import handlers.plan as ph


@pytest.fixture
def db():
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    name = "clipcut_test"
    client.drop_database(name)
    yield client[name]
    client.drop_database(name)


def _project(db, tmp_path, pid="p1"):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    db.projects.insert_one({
        "id": pid, "status": "ready", "video_path": str(src),
        "duration": 6.0, "width": 1080, "height": 1920,
        "words": [
            {"text": "This", "start": 0.0, "end": 0.3, "type": "word"},
            {"text": "laptop", "start": 0.4, "end": 0.9, "type": "word"},
            {"text": "everything", "start": 1.0, "end": 1.6, "type": "word"},
        ],
        "text": "This laptop everything",
        "cut_settings": {"pause_threshold": 0.8, "remove_fillers": True, "disabled": []},
        "reel_settings": {"aspect": "9:16", "cinematic": True, "karaoke": True,
                          "zoom_intensity": 1.0, "punch_ins": True,
                          "punch_sensitivity": 0.5, "burn_captions": True},
        "caption_style": "bold",
    })
    return pid


def test_plan_job_stores_a_plan_with_overlays(db, tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "project_dir", lambda pid: tmp_path)
    _project(db, tmp_path)
    jobs_mod.enqueue(db, "p1", "plan")
    worker_mod.run_once(db, "w1")

    doc = db.projects.find_one({"id": "p1"})
    assert doc["plan_status"] == "ready"
    assert doc["plan_provider"] == "heuristic"
    plan = doc["plan"]
    assert plan["version"] == 2
    assert isinstance(plan["overlays"], list)
    assert len(plan["overlays"]) >= 1


def test_regenerate_preserves_locked_overlays(db, tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "project_dir", lambda pid: tmp_path)
    _project(db, tmp_path)
    jobs_mod.enqueue(db, "p1", "plan")
    worker_mod.run_once(db, "w1")

    plan = db.projects.find_one({"id": "p1"})["plan"]
    locked_id = plan["overlays"][0]["id"]
    db.projects.update_one({"id": "p1", "plan.overlays.id": locked_id},
                           {"$set": {"plan.overlays.$.locked": True}})

    jobs_mod.enqueue(db, "p1", "plan", {"regenerate": True})
    worker_mod.run_once(db, "w1")

    ids = [o["id"] for o in db.projects.find_one({"id": "p1"})["plan"]["overlays"]]
    assert locked_id in ids


def test_missing_project_fails_job(db, tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "project_dir", lambda pid: tmp_path)
    jid = jobs_mod.enqueue(db, "ghost", "plan")
    worker_mod.run_once(db, "w1")
    assert db.jobs.find_one({"id": jid})["status"] == "error"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_plan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handlers.plan'`.

- [ ] **Step 3: Implement**

Create `backend/handlers/plan.py`:

```python
"""The `plan` job: transcript -> picks -> EDL v2 overlays on the project doc."""

from pathlib import Path

import worker
from cut_state import compute_cut_state
from plan import assemble, materialize
from plan import overlays as ov_mod
from plan.providers.base import PlanContext
from plan.providers.heuristic import HeuristicProvider

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def project_dir(pid: str) -> Path:
    return DATA_DIR / pid


def _provider_chain():
    # Task 8 prepends ClaudeCliProvider here.
    return [HeuristicProvider()]


def run(ctx) -> dict:
    doc = ctx.db.projects.find_one({"id": ctx.project_id})
    if not doc:
        raise RuntimeError(f"project {ctx.project_id} not found")

    ctx.progress(10, "planning")
    cut_state = compute_cut_state(doc)
    base = assemble.from_project(doc, cut_state)
    pdir = project_dir(ctx.project_id)
    materialize.write(base, pdir)

    words = doc.get("words") or []
    pctx = PlanContext(
        edit_dir=materialize.edit_dir(pdir),
        words=words,
        text=doc.get("text") or "",
        ranges=base["ranges"],
        total_s=float(base.get("total_duration_s") or 0.0),
    )

    picks = None
    used = "heuristic"
    for provider in _provider_chain():
        picks = provider.plan(pctx)
        if picks is not None:
            used = provider.name
            break
    if picks is None:
        picks = HeuristicProvider().plan(pctx)
        used = "heuristic"

    locked = []
    if (ctx.payload or {}).get("regenerate"):
        prev = (doc.get("plan") or {}).get("overlays") or []
        locked = [o for o in prev if o.get("locked")]

    base["overlays"] = ov_mod.overlays_from_picks(
        picks, base["ranges"], pctx.total_s, locked=locked,
    )

    ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
        "plan": base,
        "plan_provider": used,
        "plan_status": "ready",
    }})
    return {"provider": used, "overlays": len(base["overlays"])}


worker.HANDLERS["plan"] = run
```

Modify `backend/worker.py`'s `_register_handlers`:

```python
def _register_handlers() -> None:
    import handlers.transcribe  # noqa: F401
    import handlers.export      # noqa: F401
    import handlers.plan        # noqa: F401
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_plan.py -v`
Expected: PASS, 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/handlers/plan.py backend/worker.py backend/tests/test_handler_plan.py
git commit -m "feat: plan job produces EDL v2 overlays via the provider chain"
```

---

### Task 8: ClaudeCliProvider + the brief

**Files:**
- Create: `backend/plan/brief.py`
- Create: `backend/plan/providers/claude_cli.py`
- Modify: `backend/handlers/plan.py` (`_provider_chain` prepends Claude)
- Test: `backend/tests/test_claude_provider.py`

**Interfaces:**
- Consumes: `PlanContext`; `validate_picks`; `app/server/claude.py:_claude_bin` pattern (resolve the binary yourself — do not import the app package); `helpers/hidden_proc.py:run`.
- Produces:
  - `brief.write_brief(ctx: PlanContext) -> Path` — writes `edit_dir/brief.md` describing the transcript, the kept ranges, the catalogs, and the exact `picks.json` schema Claude must write.
  - `ClaudeCliProvider` — `name = "claude"`, `plan(ctx) -> dict | None`. Writes the brief, runs `claude` one-shot with cwd = `edit_dir`, 180s timeout (process killed on expiry), then reads and validates `edit_dir/picks.json`. Returns the validated picks, or `None` on any failure (binary missing, timeout, missing/invalid JSON).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_claude_provider.py`:

```python
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

from plan.providers.base import PlanContext, validate_picks
from plan.providers import claude_cli
from plan import brief


def _ctx(tmp_path):
    return PlanContext(
        edit_dir=tmp_path, words=[{"text": "hi", "start": 0.0, "end": 0.3}],
        text="hi", ranges=[{"source": "main", "start": 0.0, "end": 2.0}], total_s=2.0,
    )


def test_brief_is_written_and_names_picks_json(tmp_path):
    p = brief.write_brief(_ctx(tmp_path))
    assert p.is_file()
    body = p.read_text(encoding="utf-8")
    assert "picks.json" in body
    assert "visuals" in body


def test_provider_returns_validated_picks_when_claude_writes_them(tmp_path, monkeypatch):
    def fake_run(ctx):
        (ctx.edit_dir / "picks.json").write_text(json.dumps({
            "visuals": [{"kind": "broll", "after_i": 0, "query": "x", "duration_s": 2.0}]
        }), encoding="utf-8")
        return 0

    monkeypatch.setattr(claude_cli, "_invoke_claude", fake_run)
    picks = claude_cli.ClaudeCliProvider().plan(_ctx(tmp_path))
    assert picks is not None
    assert validate_picks(picks) == []


def test_provider_returns_none_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_cli, "_invoke_claude", lambda ctx: 0)
    assert claude_cli.ClaudeCliProvider().plan(_ctx(tmp_path)) is None


def test_provider_returns_none_on_invalid_json(tmp_path, monkeypatch):
    def fake_run(ctx):
        (ctx.edit_dir / "picks.json").write_text("{not json", encoding="utf-8")
        return 0

    monkeypatch.setattr(claude_cli, "_invoke_claude", fake_run)
    assert claude_cli.ClaudeCliProvider().plan(_ctx(tmp_path)) is None


def test_provider_returns_none_on_schema_violation(tmp_path, monkeypatch):
    def fake_run(ctx):
        (ctx.edit_dir / "picks.json").write_text(json.dumps({"visuals": "nope"}), encoding="utf-8")
        return 0

    monkeypatch.setattr(claude_cli, "_invoke_claude", fake_run)
    assert claude_cli.ClaudeCliProvider().plan(_ctx(tmp_path)) is None


def test_name_is_claude():
    assert claude_cli.ClaudeCliProvider().name == "claude"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_claude_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plan.brief'` / `plan.providers.claude_cli`.

- [ ] **Step 3: Implement**

Create `backend/plan/brief.py`:

```python
"""Write the brief Claude reads to produce picks.json."""

from __future__ import annotations

from pathlib import Path

_SCHEMA = """\
Write your answer to a file named `picks.json` in the current directory. It must
be a JSON object with these optional keys:

  "visuals":  a list of b-roll/still cutaways. Each item:
      {"kind": "broll" | "still", "after_i": <kept-range index int>,
       "query": "<2-4 word visual search phrase>", "duration_s": <1.2..2.6>}
  "graphics": a list of on-screen keyword pops. Each item:
      {"text": "<ONE punchy word>", "start_s": <seconds into the reel>,
       "duration_s": <1.0..2.0>}
  "cuts":     leave empty; camera zooms are handled elsewhere.

Choose visuals whose query is concretely filmable (objects, places, actions),
not abstract nouns. Place a b-roll cutaway where the speaker names a thing.
Place a graphic on the single most emphatic word of a sentence. Aim for at most
4 visuals and 3 graphics total. Do not write anything except picks.json.
"""


def write_brief(ctx) -> Path:
    lines = ["# Edit brief", "", "You are the editor for a talking-head reel.", ""]
    lines.append(f"The reel is {ctx.total_s:.1f}s across {len(ctx.ranges)} kept ranges.")
    lines.append("")
    lines.append("## Transcript")
    lines.append(ctx.text or "(no transcript)")
    lines.append("")
    lines.append("## Kept ranges (index : seconds)")
    for i, r in enumerate(ctx.ranges):
        lines.append(f"  {i}: {float(r.get('start', 0)):.2f}-{float(r.get('end', 0)):.2f}")
    lines.append("")
    lines.append("## Your task")
    lines.append(_SCHEMA)
    out = Path(ctx.edit_dir) / "brief.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
```

Create `backend/plan/providers/claude_cli.py`:

```python
"""One-shot Claude CLI provider. Writes a brief, runs claude, reads picks.json."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from plan import brief
from plan.providers.base import PlanContext, validate_picks

TIMEOUT_S = 180


def _claude_bin() -> str:
    home = Path.home()
    for cand in (home / ".local" / "bin" / "claude.exe",
                 home / ".local" / "bin" / "claude"):
        if cand.is_file():
            return str(cand)
    found = shutil.which("claude")
    return found or "claude"


def _invoke_claude(ctx: PlanContext) -> int:
    """Run claude one-shot with cwd=edit_dir. Returns the process return code."""
    prompt = "Read brief.md in this directory and follow it exactly."
    cmd = [_claude_bin(), "-p", prompt, "--permission-mode", "acceptEdits"]
    try:
        proc = subprocess.run(
            cmd, cwd=str(ctx.edit_dir), capture_output=True, text=True,
            timeout=TIMEOUT_S,
        )
        return proc.returncode
    except subprocess.TimeoutExpired:
        return -1


class ClaudeCliProvider:
    name = "claude"

    def plan(self, ctx: PlanContext) -> dict | None:
        try:
            brief.write_brief(ctx)
            _invoke_claude(ctx)
        except Exception:
            return None
        picks_path = Path(ctx.edit_dir) / "picks.json"
        if not picks_path.is_file():
            return None
        try:
            data = json.loads(picks_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if validate_picks(data) != []:
            return None
        return data
```

Modify `backend/handlers/plan.py`'s `_provider_chain`:

```python
def _provider_chain():
    from plan.providers.claude_cli import ClaudeCliProvider
    return [ClaudeCliProvider(), HeuristicProvider()]
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_claude_provider.py -v`
Expected: PASS, 6 passed.

- [ ] **Step 5: Verify the heuristic fallback still triggers under the chain**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_plan.py -v`
Expected: PASS. (Claude may or may not run in this environment; if it produces no
valid `picks.json`, the chain falls through to heuristic and `plan_provider` is
`heuristic`. If it does produce valid picks, `plan_provider` is `claude`. Update
the assertion in `test_plan_job_stores_a_plan_with_overlays` to accept either
`"heuristic"` or `"claude"` rather than only `"heuristic"`, and note why.)

- [ ] **Step 6: Commit**

```bash
git add backend/plan/brief.py backend/plan/providers/claude_cli.py backend/handlers/plan.py backend/tests/test_claude_provider.py backend/tests/test_handler_plan.py
git commit -m "feat: ClaudeCliProvider behind the provider chain, heuristic fallback"
```

---

### Task 9: Auto-plan after transcribe, and API routes

**Files:**
- Modify: `backend/handlers/transcribe.py` (enqueue a `plan` job on success)
- Modify: `backend/server.py` (`POST /api/projects/{pid}/plan`, `GET /api/projects/{pid}/plan`; attach the stored plan's overlays at export time)
- Test: `backend/tests/test_handler_transcribe.py`, `backend/tests/test_handler_export.py`

**Interfaces:**
- Consumes: `jobs.enqueue`; `handlers.plan`; the stored `project["plan"]`.
- Produces:
  - Transcription success enqueues a `plan` job for the project.
  - `POST /api/projects/{pid}/plan` with body `{regenerate: bool}` enqueues a plan job and returns `{job_id}`.
  - `GET /api/projects/{pid}/plan` returns `project["plan"]` (or 404 if none yet).
  - The export/render path attaches `project["plan"]["overlays"]` (enabled ones) onto the freshly-assembled EDL, so overlays render while cuts/captions/reframe stay driven by the current settings.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_handler_transcribe.py`:

```python
def test_transcribe_success_enqueues_a_plan_job(db, monkeypatch, tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"x")
    db.projects.insert_one({"id": "p9", "status": "transcribing", "video_path": str(video)})
    monkeypatch.setattr(
        th.transcription, "transcribe_video",
        lambda path: {"words": [{"text": "hi", "start": 0.0, "end": 0.3, "type": "word"}], "text": "hi"},
    )
    jobs_mod.enqueue(db, "p9", "transcribe")
    worker_mod.run_once(db, "w1")
    assert db.jobs.find_one({"project_id": "p9", "kind": "plan"}) is not None
```

(That test file already imports `jobs as jobs_mod` and `worker as worker_mod`; if
not, add them at the top following the existing test files' pattern.)

Append to `backend/tests/test_handler_export.py`:

```python
def test_export_attaches_stored_plan_overlays(db, monkeypatch, tmp_path):
    _project(db, "pov")
    captured = {}
    monkeypatch.setattr(eh, "project_dir", lambda pid: tmp_path)

    def fake_render(edl, pdir, out, words, progress_cb=None, cancel_cb=None):
        captured["overlays"] = edl.get("overlays")
        out.write_bytes(b"v")
        return {"width": 1080, "height": 1920, "duration": 9.0}

    monkeypatch.setattr(eh.render_plan, "render", fake_render)
    monkeypatch.setattr(eh.cloudinary_svc, "enabled", lambda: False)
    db.projects.update_one({"id": "pov"}, {"$set": {"plan": {
        "version": 2, "overlays": [
            {"id": "ov1", "kind": "broll", "start_in_output": 1.0, "duration": 2.0,
             "enabled": True, "locked": False, "file": None, "query": "x", "source": "mixkit"}
        ],
    }}})
    jobs_mod.enqueue(db, "pov", "export", {"caption_style": "bold", "reel": dict(REEL)})
    worker_mod.run_once(db, "w1")
    assert captured["overlays"] and captured["overlays"][0]["id"] == "ov1"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_handler_transcribe.py tests/test_handler_export.py -v`
Expected: the two new tests FAIL (no plan job enqueued; export EDL has no overlays).

- [ ] **Step 3: Implement**

In `backend/handlers/transcribe.py`, after the successful `update_one` that sets `status: "ready"`, enqueue a plan job:

```python
    import jobs
    jobs.enqueue(ctx.db, ctx.project_id, "plan")
```

In `backend/handlers/export.py`, after assembling the base EDL and before calling
`render_plan.render`, attach the stored plan's enabled overlays:

```python
        edl = assemble.from_project({**doc, "caption_style": style_key}, state)
        stored = (doc.get("plan") or {}).get("overlays") or []
        edl["overlays"] = [o for o in stored if o.get("enabled")]
        meta = render_plan.render(edl, pdir, out_path, words=doc.get("words") or [],
                                  progress_cb=cb, cancel_cb=ctx.cancelled)
```

In `backend/server.py`, add the two routes near the other project routes:

```python
class PlanBody(BaseModel):
    regenerate: bool = False


@api.post("/projects/{pid}/plan")
def start_plan(pid: str, body: PlanBody):
    get_project_or_404(pid)
    projects.update_one({"id": pid}, {"$set": {"plan_status": "planning"}})
    jid = jobs.enqueue(db, pid, "plan", {"regenerate": body.regenerate})
    return {"ok": True, "job_id": jid}


@api.get("/projects/{pid}/plan")
def get_plan(pid: str):
    doc = get_project_or_404(pid)
    plan = doc.get("plan")
    if not plan:
        raise HTTPException(404, "no plan yet")
    return {"plan": plan, "provider": doc.get("plan_provider"),
            "status": doc.get("plan_status")}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/ -v --ignore=tests/test_reel_backend.py --ignore=tests/test_backend_e2e.py`
Expected: PASS across the backend suite.

- [ ] **Step 5: Commit**

```bash
git add backend/handlers/transcribe.py backend/handlers/export.py backend/server.py backend/tests/test_handler_transcribe.py backend/tests/test_handler_export.py
git commit -m "feat: auto-plan after transcribe; plan routes; overlays flow into export"
```

---

### Task 10: End-to-end headless verification

**Files:**
- Create: `backend/tests/test_plan_e2e.py`
- Test: itself

**Interfaces:**
- Consumes: everything above.
- Produces: one integration test that runs the whole plan→render path on the
  committed parity fixture with the heuristic provider and asserts a b-roll/graphic
  overlay is present in the plan and that the rendered file has the overlay
  composited. Network fetching is stubbed so the test is offline and deterministic.

- [ ] **Step 1: Write the test**

Create `backend/tests/test_plan_e2e.py`:

```python
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

from plan.providers.base import PlanContext
from plan.providers.heuristic import HeuristicProvider
from plan import assemble, overlays as ov_mod, render_plan

FIXTURE = Path(__file__).parent / "fixtures" / "parity_src.mp4"

WORDS = [
    {"text": "This", "start": 0.2, "end": 0.5, "type": "word"},
    {"text": "laptop", "start": 0.6, "end": 1.1, "type": "word"},
    {"text": "everything", "start": 1.2, "end": 1.9, "type": "word"},
    {"text": "coding", "start": 3.0, "end": 3.6, "type": "word"},
]
CUT_STATE = {"keep_ranges": [(0.0, 2.0), (2.8, 4.5)], "kept_duration": 3.7, "moves": []}


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture not generated")
def test_heuristic_plan_renders_with_an_overlay(tmp_path, monkeypatch):
    # Keep it offline: force every b-roll fetch to fail so the graphic fallback
    # (pure PIL, no network) supplies the overlay clip.
    monkeypatch.setattr(ov_mod, "resolve_broll_file", lambda vis, dest: None)
    monkeypatch.setattr(ov_mod, "match_photo", lambda q, items=None: None)

    doc = {"id": "e2e", "video_path": str(FIXTURE), "caption_style": "bold",
           "reel_settings": {"aspect": "9:16", "cinematic": False, "karaoke": False,
                             "zoom_intensity": 1.0, "punch_ins": False,
                             "punch_sensitivity": 0.5, "burn_captions": False}}
    base = assemble.from_project(doc, CUT_STATE)
    ctx = PlanContext(edit_dir=tmp_path / "edit", words=WORDS, text="This laptop everything coding",
                      ranges=base["ranges"], total_s=base["total_duration_s"])
    (tmp_path / "edit").mkdir(parents=True, exist_ok=True)
    picks = HeuristicProvider().plan(ctx)
    base["overlays"] = ov_mod.overlays_from_picks(picks, base["ranges"], ctx.total_s)
    assert len(base["overlays"]) >= 1

    out = tmp_path / "out.mp4"
    meta = render_plan.render(base, tmp_path, out, words=[])
    assert out.is_file()
    assert meta["width"] == 1080 and meta["height"] == 1920
    assert meta["duration"] > 0
```

- [ ] **Step 2: Run the test**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_plan_e2e.py -v`
Expected: PASS. If the fixture is absent it skips — regenerate it per the fixture
README (the same `parity_src.mp4` Plan 1 Task 12 committed under
`backend/tests/fixtures/`).

- [ ] **Step 3: Full-suite regression**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_reel_backend.py --ignore=tests/test_backend_e2e.py`
Then: `.venv-local/Scripts/python.exe -m pytest tests/ -q` (from repo root)
Expected: backend all green; root suite unchanged except the 6 known pre-existing failures.

- [ ] **Step 4: Manual real-world check (worker + API)**

Start the three processes (`scripts\dev.ps1`), upload a real talking clip, let it
transcribe, and confirm `GET /api/projects/<pid>/plan` returns a plan with
overlays. Export and confirm a b-roll cutaway or keyword graphic appears in the
output. Extract a frame at an overlay's `start_in_output` and look at it.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_plan_e2e.py
git commit -m "test: headless end-to-end plan+render with heuristic provider"
```

---

## Self-Review

**Spec coverage.** Decision-provider seam (Task 2), HeuristicProvider (3),
ClaudeCliProvider + one-shot invocation + picks.json validation + heuristic
fallback (8), brief (8), picks→overlays with lock preservation (4), render-time
resolution of b-roll/stills/graphics with fallback (5–6), the `plan` job and
auto-plan-after-transcribe (7, 9), plan routes (9), v1-compat validation carry-
forward (1), headless e2e (10). Deferred to Plan 2b, as intended: the three-lane
review UI and the overlay `PATCH` route.

**Deliberate omissions.** No `PATCH /plan/overlays/{oid}` here — enable/lock/swap
is a UI concern and lands in 2b with the screen that drives it. Overlays are
stored with `enabled`/`locked` already so 2b adds control, not schema. `cuts`
picks are accepted by the schema but the heuristic leaves them empty, since zoom
cuts already come from `cut_state["moves"]` (restored in the plan-1 fix wave);
Claude may still populate them and `apply_cut_picks` would consume them — wiring
that is a small follow-up, flagged not built, to avoid double-driving zoom.

**Type consistency.** `PlanContext(edit_dir, words, text, ranges, total_s)` is
used identically in Tasks 2/3/7/8/10. `overlays_from_picks(picks, ranges, total_s,
locked=None)` and `resolve_overlays(overlays, edit_dir, *, fetch=True)` match
between definition (4/5) and callers (6/7). `plan(ctx) -> dict | None` holds for
both providers; the heuristic never returns `None`. The stored document keys
(`plan`, `plan_provider`, `plan_status`) are written in Task 7/9 and read in Task
9's route and export attach.

**Risks.** Task 9's export change makes the stored plan's overlays authoritative
while cuts/captions/reframe stay live from settings — verify no existing export
test asserts empty overlays. Task 8's Claude call is environment-dependent; every
test stubs `_invoke_claude`, and the one non-stubbed assertion (Task 8 Step 5)
accepts either provider name.
