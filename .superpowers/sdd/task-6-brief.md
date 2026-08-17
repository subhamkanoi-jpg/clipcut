### Task 6: Materialize plans to disk for helpers/

**Files:**
- Create: `backend/plan/materialize.py`
- Create: `backend/tests/test_materialize.py`

**Interfaces:**
- Consumes: `plan.model` from Task 5.
- Produces: `plan.materialize.edit_dir(project_dir: Path) -> Path`; `plan.materialize.write(plan: dict, project_dir: Path) -> Path` writing `edit/edl.json` with source paths made absolute; `plan.materialize.clean(project_dir: Path) -> None`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_materialize.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_materialize.py -v`
Expected: FAIL — `ImportError: cannot import name 'materialize'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/plan/materialize.py`:

```python
"""Project a Mongo-held plan onto disk so helpers/ can consume it.

Mongo is the source of truth. Everything under <project_dir>/edit/ is derived
and safe to delete at any time.
"""

import json
import shutil
from pathlib import Path


def edit_dir(project_dir: Path) -> Path:
    return Path(project_dir) / "edit"


def write(plan: dict, project_dir: Path) -> Path:
    """Write edit/edl.json with absolute source paths. Returns the file path."""
    d = edit_dir(project_dir)
    d.mkdir(parents=True, exist_ok=True)
    out = dict(plan)
    out["sources"] = {
        name: str(Path(p).resolve())
        for name, p in (plan.get("sources") or {}).items()
    }
    path = d / "edl.json"
    path.write_text(
        json.dumps(out, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def clean(project_dir: Path) -> None:
    shutil.rmtree(edit_dir(project_dir), ignore_errors=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_materialize.py -v`
Expected: PASS, 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/plan/materialize.py backend/tests/test_materialize.py
git commit -m "feat: materialize plans to edit/edl.json for helpers"
```

---

