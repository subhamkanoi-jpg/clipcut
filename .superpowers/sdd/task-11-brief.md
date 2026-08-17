### Task 11: Render an EDL v2 through the helpers pipeline

**Files:**
- Create: `backend/plan/render_plan.py`
- Create: `backend/tests/test_render_plan.py`

**Interfaces:**
- Consumes: `plan.model.validate` (Task 5), `plan.materialize.write` (Task 6), `helpers.render.extract_segment`/`concat_segments`/`build_final_composite`/`apply_loudnorm_two_pass`, `helpers.captions_ass.build_ass` (Task 8).
- Produces: `plan.render_plan.render(plan: dict, project_dir: Path, out_path: Path, words: list, progress_cb=None, cancel_cb=None) -> dict` returning `{"width", "height", "duration"}`. Raises `ValueError` listing plan errors when validation fails. Calls `cancel_cb()` before each ffmpeg spawn and raises `worker.Cancelled` when it returns True.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_render_plan.py`:

```python
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

from plan import model, render_plan


def _plan(tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    p = model.new_plan("p1", str(src))
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0, "zoom": 1.0}]
    p["total_duration_s"] = 2.0
    return p


def test_invalid_plan_raises_with_reasons(tmp_path):
    p = _plan(tmp_path)
    p["ranges"] = []
    with pytest.raises(ValueError) as exc:
        render_plan.render(p, tmp_path, tmp_path / "out.mp4", words=[])
    assert "ranges" in str(exc.value)


def test_progress_callback_reports_named_stages(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(render_plan, "_extract_all", lambda *a, **k: [tmp_path / "s0.mp4"])
    monkeypatch.setattr(render_plan, "_concat", lambda *a, **k: tmp_path / "base.mp4")
    monkeypatch.setattr(render_plan, "_composite", lambda *a, **k: tmp_path / "comp.mp4")
    monkeypatch.setattr(render_plan, "_master", lambda *a, **k: None)
    monkeypatch.setattr(render_plan, "_probe_out",
                        lambda p: {"width": 1080, "height": 1920, "duration": 2.0})

    render_plan.render(_plan(tmp_path), tmp_path, tmp_path / "out.mp4",
                       words=[], progress_cb=lambda p, s: seen.append(s))

    assert [s for s in ("cutting", "compositing", "captioning", "mastering")
            if s in seen] == ["cutting", "compositing", "captioning", "mastering"]


def test_cancel_before_extract_raises(tmp_path, monkeypatch):
    import worker

    monkeypatch.setattr(render_plan, "_extract_all",
                        lambda *a, **k: pytest.fail("must not extract"))
    with pytest.raises(worker.Cancelled):
        render_plan.render(_plan(tmp_path), tmp_path, tmp_path / "out.mp4",
                           words=[], cancel_cb=lambda: True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_render_plan.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_plan'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/plan/render_plan.py`:

```python
"""Render an EDL v2 using the helpers/ pipeline.

Stage order is fixed: cut -> concat -> composite overlays -> burn captions ->
two-pass loudnorm. Captions are always last before mastering so overlays cannot
cover them.
"""

import json
import subprocess
from pathlib import Path

import captions_ass
import render as helpers_render
import worker
from plan import materialize, model


def _probe_out(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    stream = (data.get("streams") or [{}])[0]
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration": float((data.get("format") or {}).get("duration") or 0.0),
    }


def _extract_all(plan, project_dir, work_dir, cover, center_x):
    sources = plan["sources"]
    paths = []
    for i, r in enumerate(plan["ranges"]):
        seg = work_dir / f"seg_{i:04d}.mp4"
        helpers_render.extract_segment(
            Path(sources[r["source"]]),
            float(r["start"]),
            float(r["end"]) - float(r["start"]),
            helpers_render.resolve_grade_filter(plan.get("grade")),
            seg,
            zoom=float(r.get("zoom") or 1.0),
            cover=cover,
            center_x=center_x,
        )
        paths.append(seg)
    return paths


def _concat(paths, work_dir, edit_dir):
    base = work_dir / "base.mp4"
    helpers_render.concat_segments(paths, base, edit_dir)
    return base


def _composite(base, plan, subs_path, work_dir, edit_dir):
    out = work_dir / "composite.mp4"
    overlays = [o for o in (plan.get("overlays") or []) if o.get("enabled")]
    helpers_render.build_final_composite(base, overlays, subs_path, out, edit_dir)
    return out


def _master(src, out_path):
    helpers_render.apply_loudnorm_two_pass(src, out_path)


def render(plan: dict, project_dir: Path, out_path: Path, words: list,
           progress_cb=None, cancel_cb=None) -> dict:
    errors = model.validate(plan)
    if errors:
        raise ValueError("invalid plan: " + "; ".join(errors))

    def tick(p, stage):
        if progress_cb:
            progress_cb(p, stage)

    def check_cancel():
        if cancel_cb and cancel_cb():
            raise worker.Cancelled()

    project_dir = Path(project_dir)
    edit_dir = materialize.edit_dir(project_dir)
    work_dir = edit_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    materialize.write(plan, project_dir)

    aspect = (plan.get("reframe") or {}).get("aspect", "9:16")
    cover = aspect == "9:16"
    center_x = float((plan.get("reframe") or {}).get("center_x", 0.5))

    check_cancel()
    tick(10, "cutting")
    segments = _extract_all(plan, project_dir, work_dir, cover, center_x)

    check_cancel()
    tick(55, "compositing")
    base = _concat(segments, work_dir, edit_dir)

    subs_path = None
    caps = plan.get("captions") or {}
    if caps.get("burn") and words:
        check_cancel()
        tick(70, "captioning")
        probe = _probe_out(base)
        subs_path = edit_dir / "captions.ass"
        style = captions_ass.CAPTION_STYLES.get(
            caps.get("style", "bold"), captions_ass.CAPTION_STYLES["bold"]
        )
        captions_ass.build_ass(
            words,
            # captions_ass.timeline_chunks unpacks `for r_start, r_end in ranges`,
            # so these must be (start, end) TUPLES, not dicts. EDL v2 ranges are
            # dicts, so convert here.
            [(r["start"], r["end"]) for r in plan["ranges"]],
            subs_path, style, probe["width"], probe["height"],
            karaoke=bool(caps.get("karaoke", True)),
            fonts_dir=captions_ass.FONTS_DIR,
        )
    else:
        tick(70, "captioning")

    composited = _composite(base, plan, subs_path, work_dir, edit_dir)

    check_cancel()
    tick(90, "mastering")
    _master(composited, out_path)

    tick(100, "done")
    return _probe_out(out_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_render_plan.py -v`
Expected: PASS, 3 passed

- [ ] **Step 5: Commit**

```bash
git add backend/plan/render_plan.py backend/tests/test_render_plan.py
git commit -m "feat: render EDL v2 through the helpers pipeline"
```

---

