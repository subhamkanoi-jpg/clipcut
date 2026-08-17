"""The `plan` job: transcript -> picks -> EDL v2 overlays on the project doc."""

from pathlib import Path

import worker
from cut_state import compute_cut_state
from plan import assemble, materialize
from plan import overlays as ov_mod
from plan.providers.base import PlanContext
from plan.providers.heuristic import HeuristicProvider

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def project_dir(pid: str) -> Path:
    return DATA_DIR / pid


def _provider_chain():
    from plan.providers.claude_cli import ClaudeCliProvider
    return [ClaudeCliProvider(), HeuristicProvider()]


def run(ctx) -> dict:
    doc = ctx.db.projects.find_one({"id": ctx.project_id})
    if not doc:
        raise RuntimeError(f"project {ctx.project_id} not found")

    ctx.progress(10, "planning")
    cut_state = compute_cut_state(doc)
    base = assemble.from_project(doc, cut_state)
    pdir = project_dir(ctx.project_id)
    materialize.write(base, pdir)

    words = doc.get("words") or []
    pctx = PlanContext(
        edit_dir=materialize.edit_dir(pdir),
        words=words,
        text=doc.get("text") or "",
        ranges=base["ranges"],
        total_s=float(base.get("total_duration_s") or 0.0),
    )

    picks = None
    used = "heuristic"
    for provider in _provider_chain():
        picks = provider.plan(pctx)
        if picks is not None:
            used = provider.name
            break
    # _provider_chain() always ends with a HeuristicProvider, whose .plan()
    # never returns None, so the loop above guarantees picks is set by the
    # time it exits -- there is no reachable case where picks is still None
    # here.
    assert picks is not None

    locked = []
    if (ctx.payload or {}).get("regenerate"):
        prev = (doc.get("plan") or {}).get("overlays") or []
        locked = [o for o in prev if o.get("locked")]

    base["overlays"] = ov_mod.overlays_from_picks(
        picks, base["ranges"], pctx.total_s, locked=locked,
    )

    ctx.db.projects.update_one({"id": ctx.project_id}, {"$set": {
        "plan": base,
        "plan_provider": used,
        "plan_status": "ready",
    }})
    return {"provider": used, "overlays": len(base["overlays"])}


worker.HANDLERS["plan"] = run
