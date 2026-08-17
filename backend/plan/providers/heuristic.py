"""LLM-free decision provider: keyword heuristics over the transcript.

Doubles as the fallback when Claude is unavailable and as the test double for
the whole pipeline.
"""

from __future__ import annotations

import re

from plan.providers.base import PlanContext

try:
    from zooms import HOOK_WORDS  # backend/zooms.py (flat import; backend is on sys.path)
except Exception:  # guard for import-order safety only
    HOOK_WORDS = set()

MAX_HEURISTIC_VISUALS = 3
MAX_HEURISTIC_GRAPHICS = 3
BROLL_DURATION_S = 2.2
GRAPHIC_DURATION_S = 1.6

# Words that make poor b-roll subjects even though they are long.
_STOP = {
    "about", "there", "their", "would", "could", "should", "really", "because",
    "something", "everything", "nothing", "anything", "another", "actually",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


class HeuristicProvider:
    name = "heuristic"

    def plan(self, ctx: PlanContext) -> dict:
        words = [w for w in ctx.words if w.get("type", "word") == "word"]
        n_ranges = max(1, len(ctx.ranges))

        visuals: list[dict] = []
        seen_q: set[str] = set()
        for w in words:
            norm = _norm(w.get("text", ""))
            if len(norm) < 5 or norm in _STOP or norm in seen_q:
                continue
            seen_q.add(norm)
            after_i = self._range_of(ctx, float(w.get("start") or 0.0), n_ranges)
            visuals.append({
                "kind": "broll",
                "after_i": after_i,
                "query": norm,
                "duration_s": BROLL_DURATION_S,
            })
            if len(visuals) >= MAX_HEURISTIC_VISUALS:
                break

        graphics: list[dict] = []
        for w in words:
            norm = _norm(w.get("text", ""))
            if norm in HOOK_WORDS:
                graphics.append({
                    "text": w.get("text", "").strip(),
                    "start_s": self._source_to_output(ctx, float(w.get("start") or 0.0)),
                    "duration_s": GRAPHIC_DURATION_S,
                })
            if len(graphics) >= MAX_HEURISTIC_GRAPHICS:
                break

        return {"cuts": [], "visuals": visuals, "graphics": graphics}

    @staticmethod
    def _range_of(ctx: PlanContext, t: float, n_ranges: int) -> int:
        """Which kept range does SOURCE time t fall in? Clamped to [0, n-1]."""
        for i, r in enumerate(ctx.ranges):
            dur = float(r.get("end") or 0) - float(r.get("start") or 0)
            if t < float(r.get("start") or 0) + dur:
                return i
        return n_ranges - 1

    @staticmethod
    def _source_to_output(ctx: PlanContext, t: float) -> float:
        """Map a SOURCE-timeline time t to its OUTPUT-timeline (finished
        reel) equivalent, given the kept ranges.

        Walks the kept ranges accumulating output time. If t falls within
        kept range i (r.start <= t < r.end), its output time is
        output_time_at(ranges, i) + (t - r.start) -- equivalently, the
        accumulated output duration of all prior ranges plus the offset into
        this one. If t falls in a trimmed gap (or before/after all kept
        footage), it snaps to the nearest kept boundary: the output-time end
        of the previous kept range, or 0.0 if there is none.
        """
        out_t = 0.0
        for r in ctx.ranges:
            r_start = float(r.get("start") or 0.0)
            r_end = float(r.get("end") or 0.0)
            if t < r_start:
                return max(0.0, out_t)
            if t < r_end:
                return out_t + (t - r_start)
            out_t += r_end - r_start
        return max(0.0, out_t)
