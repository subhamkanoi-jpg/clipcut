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
    """Delete the edit directory, with containment verification.

    Raises ValueError if the project_dir contains path escape attempts (..)
    or if the resolved edit path is not exactly a direct child named "edit".
    """
    project_dir_path = Path(project_dir)

    # Reject paths containing ".." to prevent traversal attacks
    if ".." in project_dir_path.parts:
        raise ValueError(
            f"Project directory path cannot contain '..' components: {project_dir_path}"
        )

    project_dir_resolved = project_dir_path.resolve()
    edit_path_resolved = edit_dir(project_dir_path).resolve()

    # Verify the edit path is a direct child of project_dir named exactly "edit"
    if edit_path_resolved.name != "edit" or edit_path_resolved.parent != project_dir_resolved:
        raise ValueError(
            f"Edit path {edit_path_resolved} is not a direct child of {project_dir_resolved}"
        )

    shutil.rmtree(edit_path_resolved, ignore_errors=True)
