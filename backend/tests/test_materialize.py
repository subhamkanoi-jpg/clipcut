import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plan import materialize, model


def test_write_creates_edl_json(tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    p = model.new_plan("p1", str(src))
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]

    out = materialize.write(p, tmp_path)

    assert out == tmp_path / "edit" / "edl.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert Path(data["sources"]["main"]).is_absolute()


def test_write_is_idempotent(tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    p = model.new_plan("p1", str(src))
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 1.0}]
    first = materialize.write(p, tmp_path).read_text(encoding="utf-8")
    second = materialize.write(p, tmp_path).read_text(encoding="utf-8")
    assert first == second


def test_clean_removes_edit_dir_but_not_source(tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    p = model.new_plan("p1", str(src))
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 1.0}]
    materialize.write(p, tmp_path)
    assert (tmp_path / "edit").exists()

    materialize.clean(tmp_path)

    assert not (tmp_path / "edit").exists()
    assert src.exists()


def test_clean_is_safe_when_nothing_exists(tmp_path):
    materialize.clean(tmp_path)  # must not raise


def test_clean_refuses_path_escaping_project_dir(tmp_path):
    """Test that clean() rejects project_dir paths with .. segments that escape containment."""
    import pytest

    # Create a real project structure
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "edit").mkdir()
    (proj / "edit" / "edl.json").write_text('{"test": "data"}')

    # Create a source file at project level that must be protected
    source = proj / "source.mp4"
    source.write_text("video content")

    # Create a sentinel file outside the intended project scope
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("MUST_NOT_DELETE")

    # Create an edit directory at the tmp_path level (outside intended project)
    tmp_edit = tmp_path / "edit"
    tmp_edit.mkdir()
    (tmp_edit / "danger.txt").write_text("could be deleted if check fails")

    # Pass a malicious project_dir containing .. that would resolve outside intended bounds
    malicious_proj_dir = proj / ".."

    # With the guard in place, this should raise ValueError
    with pytest.raises(ValueError):
        materialize.clean(malicious_proj_dir)

    # Verify nothing was deleted
    assert source.exists(), "source.mp4 should not be deleted"
    assert (proj / "edit" / "edl.json").exists(), "proj/edit/edl.json should not be deleted"
    assert sentinel.exists(), "sentinel.txt outside project should not be deleted"
    assert (tmp_edit / "danger.txt").exists(), "tmp_path/edit should not be deleted"
