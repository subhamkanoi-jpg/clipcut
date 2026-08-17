"""One-shot Claude CLI provider. Writes a brief, runs claude, reads picks.json."""

from __future__ import annotations

import json
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
            returncode = _invoke_claude(ctx)
        except Exception:
            return None
        if returncode != 0:
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
