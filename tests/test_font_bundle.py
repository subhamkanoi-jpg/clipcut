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


def test_fonts_dir_is_a_fonts_only_subdirectory():
    # helpers/render.py passes FONTS_DIR straight to ffmpeg's
    # subtitles=...:fontsdir=... filter option, and libass tries to load
    # every file in that directory as a font candidate. FONTS_DIR must
    # therefore be a dedicated subdirectory (not assets/fonts/ itself, which
    # also holds README.md) or libass logs "Error opening memory font
    # 'README.md'" on every burn.
    assert captions_ass.FONTS_DIR.name == "ttf"
    assert captions_ass.FONTS_DIR.parent.name == "fonts"
    contents = list(captions_ass.FONTS_DIR.iterdir())
    assert contents, "fonts-only subdirectory should not be empty"
    for p in contents:
        assert p.suffix.lower() in (".ttf", ".otf"), p


def test_readme_lives_outside_the_fonts_only_subdirectory():
    readme = captions_ass.FONTS_DIR.parent / "README.md"
    assert readme.is_file()
    assert readme.parent != captions_ass.FONTS_DIR
