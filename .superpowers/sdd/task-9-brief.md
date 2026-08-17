### Task 9: Bundle the caption font

**Files:**
- Create: `assets/fonts/README.md`
- Create: `assets/fonts/ClipCutSans-Bold.ttf` (downloaded, see Step 1)
- Modify: `helpers/captions_ass.py` (font name constant)
- Modify: `helpers/edl.py:96` (`default_subtitle_font`)
- Create: `tests/test_font_bundle.py`

**Interfaces:**
- Consumes: `helpers.captions_ass.CAPTION_STYLES` from Task 8.
- Produces: `helpers.captions_ass.FONTS_DIR: Path` pointing at `assets/fonts/`, and `helpers.captions_ass.font_available() -> bool`.

Rationale: `FONT = "Liberation Sans"` is not present on a default Windows install,
and libass substitutes silently rather than erroring — captions render in an
unintended typeface with no warning.

- [ ] **Step 1: Add the font file**

Download DejaVu Sans Bold (unrestricted licence, ships with a permissive
"do anything" grant) and place it at `assets/fonts/ClipCutSans-Bold.ttf`:

```bash
mkdir -p assets/fonts
curl -L -o assets/fonts/ClipCutSans-Bold.ttf \
  https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf
```

Create `assets/fonts/README.md`:

```markdown
# Bundled fonts

`ClipCutSans-Bold.ttf` is DejaVu Sans Bold, renamed for a stable internal family
name. DejaVu is released under a permissive licence allowing redistribution and
modification; see https://dejavu-fonts.github.io/License.html.

It is bundled because libass silently substitutes a missing font rather than
failing, which would ship captions in the wrong typeface with no error.
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_font_bundle.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helpers"))

import captions_ass


def test_fonts_dir_exists():
    assert captions_ass.FONTS_DIR.is_dir()


def test_bundled_font_file_present():
    assert captions_ass.font_available() is True


def test_every_style_uses_the_bundled_family():
    for name, style in captions_ass.CAPTION_STYLES.items():
        assert style["font"] == captions_ass.FONT, name
```

- [ ] **Step 3: Run test to verify it fails**

Run: `../.venv-local/Scripts/python.exe -m pytest tests/test_font_bundle.py -v`
Expected: FAIL — `AttributeError: module 'captions_ass' has no attribute 'FONTS_DIR'`

- [ ] **Step 4: Write minimal implementation**

Add near the top of `helpers/captions_ass.py`:

```python
FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
FONT_FILE = FONTS_DIR / "ClipCutSans-Bold.ttf"
FONT = "DejaVu Sans"           # the family name inside the bundled TTF
FONT_FALLBACK = "Liberation Sans"


def font_available() -> bool:
    return FONT_FILE.is_file()
```

Ensure every entry in `CAPTION_STYLES` uses `"font": FONT`.

Modify `helpers/edl.py:96`:

```python
def default_subtitle_font() -> str:
    try:
        import captions_ass
        return captions_ass.FONT
    except Exception:
        return "Liberation Sans"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `../.venv-local/Scripts/python.exe -m pytest tests/test_font_bundle.py -v`
Expected: PASS, 3 passed

- [ ] **Step 6: Verify ffmpeg actually resolves the bundled font**

```bash
../.venv-local/Scripts/python.exe -c "
import sys, pathlib
sys.path.insert(0, 'helpers')
import captions_ass as c
print('font file:', c.FONT_FILE, c.font_available())
"
```
Expected: prints the path and `True`.

- [ ] **Step 7: Commit**

```bash
git add assets/fonts helpers/captions_ass.py helpers/edl.py tests/test_font_bundle.py
git commit -m "feat: bundle caption font instead of relying on system Liberation Sans"
```

---

