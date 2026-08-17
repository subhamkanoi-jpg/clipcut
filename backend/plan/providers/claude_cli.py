"""One-shot Claude CLI provider. Writes a brief, runs claude, reads picks.json."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from plan import brief
from plan.providers.base import PlanContext, validate_picks

log = logging.getLogger(__name__)

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
        except Exception as exc:
            log.info("claude_cli.plan: exception invoking claude: %s", exc)
            return None
        if returncode != 0:
            # Covers both a non-zero exit and _invoke_claude's -1 sentinel
            # for a timed-out process.
            log.info("claude_cli.plan: claude exited with code %s", returncode)
            return None
        picks_path = Path(ctx.edit_dir) / "picks.json"
        if not picks_path.is_file():
            log.info("claude_cli.plan: picks.json not found at %s", picks_path)
            return None
        try:
            data = json.loads(picks_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.info("claude_cli.plan: invalid JSON in picks.json: %s", exc)
            return None
        errors = validate_picks(data)
        if errors != []:
            log.info("claude_cli.plan: picks.json failed schema validation: %s", errors)
            return None
        return data
