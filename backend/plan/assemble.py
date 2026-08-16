"""Turn a project document plus computed cuts into an EDL v2."""

from plan import model


def from_project(doc: dict, cut_state: dict) -> dict:
    plan = model.new_plan(doc["id"], doc.get("video_path") or "")
    reel = doc.get("reel_settings") or {}

    ranges = []
    total = 0.0
    for start, end in cut_state.get("keep_ranges") or []:
        start = float(start)
        end = float(end)
        ranges.append({"source": "main", "start": start, "end": end, "zoom": 1.0})
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
