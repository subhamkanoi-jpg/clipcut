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
