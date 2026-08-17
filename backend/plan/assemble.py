"""Turn a project document plus computed cuts into an EDL v2."""

from plan import model


def from_project(doc: dict, cut_state: dict) -> dict:
    plan = model.new_plan(doc["id"], doc.get("video_path") or "")
    reel = doc.get("reel_settings") or {}

    # cut_state["moves"] (backend/cut_state.py::compute_cut_state) is computed
    # by zooms.plan(words, keep_ranges, ...) — one entry per element of
    # keep_ranges, in the same order, empty entirely when cinematic zoom is
    # off. So moves[i] lines up 1:1 with keep_ranges[i] below; no reindexing
    # needed. Each entry carries the z0->z1 zoom ramp plus punch-in snaps that
    # helpers.render.extract_segment's `move` param consumes.
    moves = cut_state.get("moves") or []

    ranges = []
    total = 0.0
    for i, (start, end) in enumerate(cut_state.get("keep_ranges") or []):
        start = float(start)
        end = float(end)
        entry = {"source": "main", "start": start, "end": end, "zoom": 1.0}
        if i < len(moves) and moves[i]:
            move = moves[i]
            entry["variation"] = move.get("kind")
            entry["move"] = {k: v for k, v in move.items() if k != "index"}
        ranges.append(entry)
        total += end - start
    plan["ranges"] = ranges
    plan["total_duration_s"] = round(total, 3)

    plan["reframe"] = {
        "aspect": reel.get("aspect", "9:16"),
        "center_x": float(doc.get("subject_center_x", 0.5)),
    }
    plan["captions"] = {
        "style": doc.get("caption_style", "bold"),
        "karaoke": bool(reel.get("karaoke", True)),
        "burn": bool(reel.get("burn_captions", True)),
    }
    return plan
