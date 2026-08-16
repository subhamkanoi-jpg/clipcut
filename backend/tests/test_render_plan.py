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
