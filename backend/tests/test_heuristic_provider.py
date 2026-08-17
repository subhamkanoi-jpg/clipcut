import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

from plan.providers import base
from plan.providers.heuristic import HeuristicProvider


def _ctx(tmp_path, words):
    text = " ".join(w["text"] for w in words)
    ranges = [{"source": "main", "start": 0.0, "end": 6.0}]
    return base.PlanContext(edit_dir=tmp_path, words=words, text=text, ranges=ranges, total_s=6.0)


WORDS = [
    {"text": "This", "start": 0.0, "end": 0.3, "type": "word"},
    {"text": "laptop", "start": 0.4, "end": 0.9, "type": "word"},
    {"text": "changes", "start": 1.0, "end": 1.4, "type": "word"},
    {"text": "everything", "start": 1.5, "end": 2.1, "type": "word"},
    {"text": "about", "start": 2.2, "end": 2.5, "type": "word"},
    {"text": "coding", "start": 2.6, "end": 3.2, "type": "word"},
]


def test_always_returns_valid_picks(tmp_path):
    picks = HeuristicProvider().plan(_ctx(tmp_path, WORDS))
    assert base.validate_picks(picks) == []


def test_produces_at_least_one_visual(tmp_path):
    picks = HeuristicProvider().plan(_ctx(tmp_path, WORDS))
    assert len(picks["visuals"]) >= 1
    v = picks["visuals"][0]
    assert v["kind"] in base.VISUAL_KINDS
    assert v["query"]
    assert 0 <= int(v["after_i"]) < 1  # only one range in this ctx


def test_hook_word_becomes_a_graphic(tmp_path):
    picks = HeuristicProvider().plan(_ctx(tmp_path, WORDS))
    texts = [g["text"].lower() for g in picks["graphics"]]
    assert "everything" in texts  # 'everything' is in zooms.HOOK_WORDS


def test_empty_transcript_is_valid_and_empty(tmp_path):
    picks = HeuristicProvider().plan(_ctx(tmp_path, []))
    assert base.validate_picks(picks) == []
    assert picks["visuals"] == []
    assert picks["graphics"] == []


def test_name_is_heuristic():
    assert HeuristicProvider().name == "heuristic"
