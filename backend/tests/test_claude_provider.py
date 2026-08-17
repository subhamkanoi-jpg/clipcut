import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

from plan.providers.base import PlanContext, validate_picks
from plan.providers import claude_cli
from plan import brief


def _ctx(tmp_path):
    return PlanContext(
        edit_dir=tmp_path, words=[{"text": "hi", "start": 0.0, "end": 0.3}],
        text="hi", ranges=[{"source": "main", "start": 0.0, "end": 2.0}], total_s=2.0,
    )


def test_brief_is_written_and_names_picks_json(tmp_path):
    p = brief.write_brief(_ctx(tmp_path))
    assert p.is_file()
    body = p.read_text(encoding="utf-8")
    assert "picks.json" in body
    assert "visuals" in body


def test_provider_returns_validated_picks_when_claude_writes_them(tmp_path, monkeypatch):
    def fake_run(ctx):
        (ctx.edit_dir / "picks.json").write_text(json.dumps({
            "visuals": [{"kind": "broll", "after_i": 0, "query": "x", "duration_s": 2.0}]
        }), encoding="utf-8")
        return 0

    monkeypatch.setattr(claude_cli, "_invoke_claude", fake_run)
    picks = claude_cli.ClaudeCliProvider().plan(_ctx(tmp_path))
    assert picks is not None
    assert validate_picks(picks) == []


def test_provider_returns_none_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_cli, "_invoke_claude", lambda ctx: 0)
    assert claude_cli.ClaudeCliProvider().plan(_ctx(tmp_path)) is None


def test_provider_returns_none_on_invalid_json(tmp_path, monkeypatch):
    def fake_run(ctx):
        (ctx.edit_dir / "picks.json").write_text("{not json", encoding="utf-8")
        return 0

    monkeypatch.setattr(claude_cli, "_invoke_claude", fake_run)
    assert claude_cli.ClaudeCliProvider().plan(_ctx(tmp_path)) is None


def test_provider_returns_none_on_schema_violation(tmp_path, monkeypatch):
    def fake_run(ctx):
        (ctx.edit_dir / "picks.json").write_text(json.dumps({"visuals": "nope"}), encoding="utf-8")
        return 0

    monkeypatch.setattr(claude_cli, "_invoke_claude", fake_run)
    assert claude_cli.ClaudeCliProvider().plan(_ctx(tmp_path)) is None


def test_name_is_claude():
    assert claude_cli.ClaudeCliProvider().name == "claude"
