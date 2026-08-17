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

    # "compositing" must land on the real composite pass, which only starts
    # after captions are built -- not on the cheap concat that precedes both
    # (see Finding 6: this used to be ticked as "compositing" before "cutting"
    # had even finished captioning, which was still ticked "captioning" but
    # AFTER the mislabeled "compositing" tick).
    assert [s for s in ("cutting", "captioning", "compositing", "mastering")
            if s in seen] == ["cutting", "captioning", "compositing", "mastering"]


def test_extraction_reports_per_segment_progress_across_cutting_range(tmp_path, monkeypatch):
    # Finding 6: the old renderer reported progress per extracted segment;
    # the EDL v2 pipeline ticked "cutting" once at 10% and then nothing until
    # "compositing" at 55%, leaving the bar frozen through the longest stage
    # (and no heartbeat -- see Finding 5c). Segment extraction must now tick
    # multiple times, monotonically, spanning roughly 10 -> 55.
    monkeypatch.setattr(render_plan.helpers_render, "extract_segment", lambda *a, **k: None)
    monkeypatch.setattr(render_plan, "_concat", lambda *a, **k: tmp_path / "base.mp4")
    monkeypatch.setattr(render_plan, "_composite", lambda *a, **k: tmp_path / "comp.mp4")
    monkeypatch.setattr(render_plan, "_master", lambda *a, **k: None)
    monkeypatch.setattr(render_plan, "_probe_out",
                        lambda p: {"width": 1080, "height": 1920, "duration": 2.0})

    plan = _plan(tmp_path)
    plan["ranges"] = [
        {"source": "main", "start": 0.0, "end": 1.0, "zoom": 1.0},
        {"source": "main", "start": 1.0, "end": 2.0, "zoom": 1.0},
        {"source": "main", "start": 2.0, "end": 3.0, "zoom": 1.0},
        {"source": "main", "start": 3.0, "end": 4.0, "zoom": 1.0},
    ]
    plan["total_duration_s"] = 4.0

    seen = []
    render_plan.render(plan, tmp_path, tmp_path / "out.mp4", words=[],
                       progress_cb=lambda p, s: seen.append((p, s)))

    cutting_ticks = [p for p, s in seen if s == "cutting"]
    assert len(cutting_ticks) > 1, "extraction must tick more than once across 4 segments"
    assert cutting_ticks == sorted(cutting_ticks), "progress must be monotonic"
    assert cutting_ticks[0] >= 10
    assert cutting_ticks[-1] <= 55


def test_cancel_before_extract_raises(tmp_path, monkeypatch):
    import worker

    monkeypatch.setattr(render_plan, "_extract_all",
                        lambda *a, **k: pytest.fail("must not extract"))
    with pytest.raises(worker.Cancelled):
        render_plan.render(_plan(tmp_path), tmp_path, tmp_path / "out.mp4",
                           words=[], cancel_cb=lambda: True)


def test_cancel_checked_before_each_extract_segment(tmp_path, monkeypatch):
    import worker

    extract_calls = []

    def fake_extract_segment(*a, **k):
        extract_calls.append(1)

    monkeypatch.setattr(render_plan.helpers_render, "extract_segment", fake_extract_segment)

    plan = _plan(tmp_path)
    plan["ranges"] = [
        {"source": "main", "start": 0.0, "end": 1.0, "zoom": 1.0},
        {"source": "main", "start": 1.0, "end": 2.0, "zoom": 1.0},
        {"source": "main", "start": 2.0, "end": 3.0, "zoom": 1.0},
    ]
    plan["total_duration_s"] = 3.0

    # Cancel becomes true only after the first segment has been extracted, so
    # a cancel check performed once before the whole loop would never see it.
    # A per-segment check must catch it before the second ffmpeg spawn.
    cancel_calls = []

    def check_cancel():
        cancel_calls.append(1)
        if len(extract_calls) >= 1:
            raise worker.Cancelled()

    with pytest.raises(worker.Cancelled):
        render_plan._extract_all(plan, tmp_path / "work", cover=False, center_x=0.5,
                                 check_cancel=check_cancel)

    assert len(extract_calls) == 1
    assert len(cancel_calls) == 2


def test_captions_pass_tuple_ranges_to_build_ass(tmp_path, monkeypatch):
    captured = {}

    def fake_build_ass(words, ranges, out_path, style, width, height,
                       karaoke=True, fonts_dir=None):
        captured["words"] = words
        captured["ranges"] = ranges
        out_path.write_text("fake ass content")
        return 3

    monkeypatch.setattr(render_plan.captions_ass, "build_ass", fake_build_ass)
    monkeypatch.setattr(render_plan, "_extract_all", lambda *a, **k: [tmp_path / "s0.mp4"])
    monkeypatch.setattr(render_plan, "_concat", lambda *a, **k: tmp_path / "base.mp4")

    composite_calls = {}

    def fake_composite(base, plan, subs_path, work_dir, edit_dir):
        composite_calls["subs_path"] = subs_path
        return tmp_path / "comp.mp4"

    monkeypatch.setattr(render_plan, "_composite", fake_composite)
    monkeypatch.setattr(render_plan, "_master", lambda *a, **k: None)
    monkeypatch.setattr(render_plan, "_probe_out",
                        lambda p: {"width": 1080, "height": 1920, "duration": 2.0})

    plan = _plan(tmp_path)
    words = [
        {"type": "word", "start": 0.1, "end": 0.4, "text": "hello"},
        {"type": "word", "start": 0.5, "end": 0.9, "text": "world"},
    ]

    render_plan.render(plan, tmp_path, tmp_path / "out.mp4", words=words)

    assert render_plan.captions_ass.build_ass is fake_build_ass
    assert captured["ranges"] == [(0.0, 2.0)]
    assert all(type(r) is tuple for r in captured["ranges"])
    assert composite_calls["subs_path"] is not None


def test_empty_captions_do_not_reach_composite(tmp_path, monkeypatch):
    def fake_build_ass(words, ranges, out_path, style, width, height,
                       karaoke=True, fonts_dir=None):
        # Matches captions_ass.build_ass's real behaviour when no chunks
        # overlap the kept ranges: an empty file, zero events.
        out_path.write_text("")
        return 0

    monkeypatch.setattr(render_plan.captions_ass, "build_ass", fake_build_ass)
    monkeypatch.setattr(render_plan, "_extract_all", lambda *a, **k: [tmp_path / "s0.mp4"])
    monkeypatch.setattr(render_plan, "_concat", lambda *a, **k: tmp_path / "base.mp4")

    composite_calls = {}

    def fake_composite(base, plan, subs_path, work_dir, edit_dir):
        composite_calls["subs_path"] = subs_path
        return tmp_path / "comp.mp4"

    monkeypatch.setattr(render_plan, "_composite", fake_composite)
    monkeypatch.setattr(render_plan, "_master", lambda *a, **k: None)
    monkeypatch.setattr(render_plan, "_probe_out",
                        lambda p: {"width": 1080, "height": 1920, "duration": 2.0})

    plan = _plan(tmp_path)
    words = [{"type": "word", "start": 0.1, "end": 0.4, "text": "hello"}]

    render_plan.render(plan, tmp_path, tmp_path / "out.mp4", words=words)

    assert composite_calls["subs_path"] is None


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
