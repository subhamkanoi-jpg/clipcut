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
    # Finding 3: a degraded overlay must be surfaced, not silent.
    assert out[0]["degraded"] is True


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


def test_broll_image_result_is_converted_to_clip(tmp_path, monkeypatch):
    photo = tmp_path / "beach.jpg"
    photo.write_bytes(b"jpeg")
    calls = []

    def _fake_photo_to_clip(p, dest, dur):
        calls.append(Path(p))
        dest.write_bytes(b"clip")
        return dest

    monkeypatch.setattr(ov_mod, "resolve_broll_file", lambda vis, dest: photo)
    monkeypatch.setattr(ov_mod, "photo_to_clip", _fake_photo_to_clip)
    ov = model.overlay("broll", 2.0, 2.4, query="beach", source="mixkit", after_i=1)
    out = ov_mod.resolve_overlays([ov], tmp_path)
    result = Path(out[0]["file"])
    assert result.suffix == ".mp4"
    assert result.is_file()
    assert calls and calls[0] == photo


def test_helper_exception_falls_back_to_graphic(tmp_path, monkeypatch):
    def _raise(vis, dest):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(ov_mod, "resolve_broll_file", _raise)
    monkeypatch.setattr(ov_mod, "make_keyword_graphic",
                        lambda text, ed, dur: _graphic_clip(ed, text, dur))
    ov = model.overlay("broll", 2.0, 2.4, query="laptop", source="mixkit", after_i=1)
    out = ov_mod.resolve_overlays([ov], tmp_path)
    assert out[0]["file"] and Path(out[0]["file"]).is_file()


def test_one_bad_overlay_does_not_abort_the_batch(tmp_path, monkeypatch):
    good = tmp_path / "good.mp4"
    good.write_bytes(b"video")

    def _resolve_broll(vis, dest):
        if vis.get("query") == "bad":
            raise RuntimeError("boom")
        return good

    monkeypatch.setattr(ov_mod, "resolve_broll_file", _resolve_broll)
    monkeypatch.setattr(ov_mod, "make_keyword_graphic",
                        lambda text, ed, dur: _graphic_clip(ed, text, dur))
    ov_bad = model.overlay("broll", 0.0, 2.0, query="bad", source="mixkit", after_i=0)
    ov_good = model.overlay("broll", 2.0, 2.0, query="good", source="mixkit", after_i=1)
    out = ov_mod.resolve_overlays([ov_bad, ov_good], tmp_path)
    assert out[0]["file"] and Path(out[0]["file"]).is_file()
    assert out[1]["file"] and Path(out[1]["file"]).is_file()
