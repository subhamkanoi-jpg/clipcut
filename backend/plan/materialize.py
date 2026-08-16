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
