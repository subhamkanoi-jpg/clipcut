"""Write the brief Claude reads to produce picks.json."""

from __future__ import annotations

from pathlib import Path

_SCHEMA = """\
Write your answer to a file named `picks.json` in the current directory. It must
be a JSON object with these optional keys:

  "visuals":  a list of b-roll/still cutaways. Each item:
      {"kind": "broll" | "still", "after_i": <kept-range index int>,
       "query": "<2-4 word visual search phrase>", "duration_s": <1.2..2.6>}
  "graphics": a list of on-screen keyword pops. Each item:
      {"text": "<ONE punchy word>", "start_s": <seconds into the reel>,
       "duration_s": <1.0..2.0>}
  "cuts":     leave empty; camera zooms are handled elsewhere.

Choose visuals whose query is concretely filmable (objects, places, actions),
not abstract nouns. Place a b-roll cutaway where the speaker names a thing.
Place a graphic on the single most emphatic word of a sentence. Aim for at most
4 visuals and 3 graphics total. Do not write anything except picks.json.
"""


def write_brief(ctx) -> Path:
    lines = ["# Edit brief", "", "You are the editor for a talking-head reel.", ""]
    lines.append(f"The reel is {ctx.total_s:.1f}s across {len(ctx.ranges)} kept ranges.")
    lines.append("")
    lines.append("## Transcript")
    lines.append(ctx.text or "(no transcript)")
    lines.append("")
    lines.append("## Kept ranges (index : seconds)")
    for i, r in enumerate(ctx.ranges):
        lines.append(f"  {i}: {float(r.get('start', 0)):.2f}-{float(r.get('end', 0)):.2f}")
    lines.append("")
    lines.append("## Your task")
    lines.append(_SCHEMA)
    out = Path(ctx.edit_dir) / "brief.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
