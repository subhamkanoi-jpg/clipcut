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
