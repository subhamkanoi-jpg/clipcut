# Bundled fonts

`ttf/ClipCutSans-Bold.ttf` is DejaVu Sans Bold, renamed for a stable internal
family name. DejaVu is released under a permissive licence allowing
redistribution and modification; see
https://dejavu-fonts.github.io/License.html.

It is bundled because libass silently substitutes a missing font rather than
failing, which would ship captions in the wrong typeface with no error.

The font file lives in the `ttf/` subdirectory (not directly in this
directory) because `helpers/render.py` passes `ttf/` as ffmpeg's
`subtitles=...:fontsdir=...` value, and libass tries to load every file in
that directory as a font candidate. Keeping this README one level up avoids
`Error opening memory font 'README.md'` noise in the ffmpeg/libass log.

Source: `dejavu-fonts-ttf-2.37.zip` from the official GitHub release
(`dejavu-fonts/dejavu-fonts`, tag `version_2_37`,
https://github.com/dejavu-fonts/dejavu-fonts/releases/tag/version_2_37),
`ttf/DejaVuSans-Bold.ttf`, copied byte-for-byte and renamed on disk only (the
file's internal family name is unchanged and still reads `DejaVu Sans`).
