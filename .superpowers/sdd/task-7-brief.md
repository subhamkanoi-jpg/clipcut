### Task 7: Face-centered crop in the helpers renderer

**Files:**
- Modify: `helpers/render.py:167-200` (`extract_segment` signature and cover branch)
- Create: `tests/test_render_cover.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `helpers.render.extract_segment(..., cover: bool = False, center_x: float = 0.5)` and a new pure helper `helpers.render.cover_crop_filter(src_w, src_h, center_x, draft=False) -> str` returning the ffmpeg `scale=...,crop=...` chain. The pure helper is what tests target; `extract_segment` calls it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_render_cover.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))

import render


def test_centered_crop_matches_legacy_behaviour():
    f = render.cover_crop_filter(1920, 1080, center_x=0.5)
    assert "crop=1080:1920" in f
    # A centred subject on a 1920x1080 source: crop x offset lands mid-frame.
    assert ":x=" in f


def test_off_centre_subject_shifts_crop_left():
    left = render.cover_crop_filter(1920, 1080, center_x=0.2)
    centre = render.cover_crop_filter(1920, 1080, center_x=0.5)
    x_left = int(left.split(":x=")[1].split(":")[0])
    x_centre = int(centre.split(":x=")[1].split(":")[0])
    assert x_left < x_centre


def test_crop_never_leaves_the_frame():
    for cx in (0.0, 0.01, 0.99, 1.0):
        f = render.cover_crop_filter(1920, 1080, center_x=cx)
        x = int(f.split(":x=")[1].split(":")[0])
        assert x >= 0
        assert x + 1080 <= 1920


def test_draft_uses_smaller_canvas():
    assert "720:1280" in render.cover_crop_filter(1920, 1080, 0.5, draft=True)


def test_already_vertical_source_is_not_cropped_horizontally():
    f = render.cover_crop_filter(1080, 1920, center_x=0.5)
    x = int(f.split(":x=")[1].split(":")[0])
    assert x == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../.venv-local/Scripts/python.exe -m pytest tests/test_render_cover.py -v`
Expected: FAIL — `AttributeError: module 'render' has no attribute 'cover_crop_filter'`

- [ ] **Step 3: Write minimal implementation**

Add to `helpers/render.py`, above `extract_segment`:

```python
def cover_crop_filter(src_w: int, src_h: int, center_x: float = 0.5,
                      draft: bool = False) -> str:
    """Scale-to-cover then crop to a vertical canvas, keeping center_x in frame.

    center_x is the subject's horizontal position as a fraction of source width.
    The crop window is clamped so it never runs past either edge.
    """
    out_w, out_h = (720, 1280) if draft else (1080, 1920)
    if not src_w or not src_h:
        return f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h}:x=0:y=0"

    # Scale so both dimensions cover the canvas.
    scale_f = max(out_w / src_w, out_h / src_h)
    scaled_w = int(round(src_w * scale_f))
    scaled_h = int(round(src_h * scale_f))
    scaled_w += scaled_w % 2
    scaled_h += scaled_h % 2

    x = int(round(center_x * scaled_w - out_w / 2))
    x = max(0, min(x, max(0, scaled_w - out_w)))
    y = max(0, (scaled_h - out_h) // 4)  # bias upward; heads sit high in frame

    return f"scale={scaled_w}:{scaled_h},crop={out_w}:{out_h}:x={x}:y={y}"
```

Change the `extract_segment` signature to add `center_x: float = 0.5` after
`cover: bool = False`, and replace the cover branch:

```python
    portrait = is_portrait_source(source)
    if cover:
        size = probe_video_size(source) or (1920, 1080)
        scale = cover_crop_filter(size[0], size[1], center_x, draft=draft)
    elif draft:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `../.venv-local/Scripts/python.exe -m pytest tests/test_render_cover.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Run the existing helpers suite for regressions**

Run: `../.venv-local/Scripts/python.exe -m pytest tests/ -v`
Expected: PASS. `cover` defaults to centred, so prior behaviour is unchanged.

- [ ] **Step 6: Commit**

```bash
git add helpers/render.py tests/test_render_cover.py
git commit -m "feat: face-centered cover crop in helpers renderer"
```

---

