# Task 9 Report: Bundle the caption font

## Status: DONE

## What was implemented

1. **`assets/fonts/ClipCutSans-Bold.ttf`** — DejaVu Sans Bold, downloaded from
   the official `dejavu-fonts/dejavu-fonts` GitHub release (tag
   `version_2_37`), asset `dejavu-fonts-ttf-2.37.zip`, file
   `ttf/DejaVuSans-Bold.ttf`, extracted and copied byte-for-byte to the target
   path (filename changed, font bytes/internal name unchanged). 705,684 bytes.

   **Deviation from the brief's literal Step 1 command**: the brief's
   suggested URL
   (`https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf`)
   returned HTTP 404 — that source repo's `master` branch is the FontForge
   *source* (`.sfd` build sources under `src/`), it has no `ttf/` directory,
   so the URL 404'd and `curl -L` happily wrote the GitHub 404 HTML page to
   the `.ttf` path. I caught this by checking `HTTP_STATUS` and inspecting
   the downloaded bytes (they were HTML, not a font). I located the correct
   source via the GitHub Releases API for the same repo/org
   (`dejavu-fonts/dejavu-fonts`, tag `version_2_37`) and downloaded the
   official build artifact `dejavu-fonts-ttf-2.37.zip` instead, extracting
   just `DejaVuSans-Bold.ttf`. Same font, same licence, same org — only the
   fetch mechanism changed (release asset instead of raw source-tree path).

2. **`assets/fonts/README.md`** — created per the brief's template, with one
   addition: a "Source" paragraph recording exactly where the bytes came from
   (release tag, zip name, internal path) since the originally-specified URL
   didn't pan out and I wanted the provenance to be auditable from the repo
   itself.

3. **`helpers/captions_ass.py`** — added near the top:
   ```python
   FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
   FONT_FILE = FONTS_DIR / "ClipCutSans-Bold.ttf"
   FONT = "DejaVu Sans"           # the family name inside the bundled TTF
   FONT_FALLBACK = "Liberation Sans"

   def font_available() -> bool:
       return FONT_FILE.is_file()
   ```
   Replacing the Task-8 placeholder `FONT = "ClipCut Sans"`. All four
   `CAPTION_STYLES` entries already referenced `"font": FONT` (Task 8 wrote
   it that way), so they picked up the real family name automatically —
   no per-style edits needed.

4. **`helpers/edl.py`** — `default_subtitle_font()` changed from a
   `sys.platform`-guessing function (`"Arial"` on win32, `"Helvetica"`
   elsewhere) to deferring to `captions_ass.FONT`, falling back to
   `"Liberation Sans"` only if the import fails, exactly as the brief
   specifies. Removed the now-unused `import sys` from the top of the file
   (it had no other use).

5. **`tests/test_font_bundle.py`** — created verbatim from the brief's Step 2
   (3 tests: `FONTS_DIR` is a dir, `font_available()` is `True`, every
   `CAPTION_STYLES` entry's `"font"` equals `FONT`).

## Font family name verification (brief's explicit ask — do not take it on faith)

Read with Pillow, as the brief suggested:
```python
from PIL import ImageFont
f = ImageFont.truetype(r"assets\fonts\ClipCutSans-Bold.ttf", 32)
print(f.getname())
# -> ('DejaVu Sans', 'Bold')
```
Family name is **`DejaVu Sans`** — matches the brief's assumption exactly, so
`FONT = "DejaVu Sans"` as specified, no divergence needed.

I also cross-checked this isn't just a Pillow quirk by actually burning a
caption with ffmpeg/libass (see "Step 6+" below) and watching libass's own
font-resolution log line name the family it matched against — same string,
independently confirmed by a second code path (fontconfig/directwrite via
libass, not Pillow/freetype).

## TDD evidence

- **Step 2/3 (red)**: wrote `tests/test_font_bundle.py` before touching
  `captions_ass.py`. Ran it — 2 of 3 failed with
  `AttributeError: module 'captions_ass' has no attribute 'FONTS_DIR'` /
  `'font_available'` (the third, family-match test, passed trivially since
  Task 8 already wired `"font": FONT` into every style — expected, not a
  gap).
- **Step 4 (implementation)**: added `FONTS_DIR`, `FONT_FILE`, `FONT`,
  `font_available()` to `captions_ass.py`; updated `edl.py`.
- **Step 5 (green)**: `pytest tests/test_font_bundle.py -v` → **3 passed**.
- **Step 6 (brief's literal check)**:
  ```
  font file: D:\Desktop\Desktop Files\Projects\clipcut\assets\fonts\ClipCutSans-Bold.ttf True
  ```
  Matches the brief's expected output exactly.
- **Beyond Step 6 — real ffmpeg/libass burn** (the task's title is "Verify
  ffmpeg actually resolves the bundled font"; the brief's literal Step 6 only
  checks file existence, not resolution, so I went further): generated a real
  `.ass` file via `captions_ass.build_ass(...)`, then ran
  `ffmpeg -f lavfi -i color=... -vf "ass=<path>:fontsdir=<FONTS_DIR>" ...`
  with `-loglevel verbose`. Exit code 0, and the libass log line reads:
  ```
  [Parsed_ass_0 @ ...] fontselect: (DejaVu Sans, 700, 0) -> DejaVuSans-Bold, 0, DejaVuSans-Bold
  ```
  This is libass matching the ASS style's `Fontname: DejaVu Sans` weight 700
  (bold) request directly against the file we bundled, via `fontsdir`, with
  no substitution warning. This is the actual mechanism a burn call would use
  at render time, not just a file-existence check.

## Root-suite regression

Baseline (confirmed before touching anything): **91 passed / 6 failed**
(`test_api.py` x2, `test_claude.py::test_first_turn_has_no_resume`,
`test_cut_picks.py::test_apply_cut_picks_zooms_and_drops`,
`test_talking_head.py::test_apply_bin_inserts_broll`,
`test_visual_picks.py::test_apply_visuals_inserts_broll_and_graphic`).

After implementation, before fixing a regression: **7 failed** — the 6
baseline failures plus a new one, `test_edl.py::test_default_font_windows`,
which asserted the *old* platform-guessing behavior (`"Arial"` on win32,
`"Helvetica"` on darwin) that Task 9 deliberately replaces. This is a real,
expected consequence of the brief's mandated `edl.py` change, not a bug in my
code — the old test encoded behavior the task explicitly obsoletes. It
wasn't in the brief's "Files" list, but the task's acceptance bar ("failure
list must be exactly those 6") requires it not regress, so I updated it.

Fix: replaced `test_default_font_windows` in `tests/test_edl.py` with two
tests matching the new contract — `test_default_font_uses_bundled_family`
(returns `captions_ass.FONT` regardless of `sys.platform`) and
`test_default_font_falls_back_when_captions_ass_unavailable` (returns
`"Liberation Sans"` when `captions_ass` can't be imported, simulated via
`monkeypatch.setitem(sys.modules, "captions_ass", None)`).

**Final root-suite run: 95 passed / 6 failed — the exact same 6 baseline
failures, zero new regressions.** (91 baseline + 3 new font_bundle tests + 1
net new edl test = 95.)

## Files changed

- `assets/fonts/ClipCutSans-Bold.ttf` (new, binary, 705,684 bytes)
- `assets/fonts/README.md` (new)
- `helpers/captions_ass.py` (modified — `FONT` constant + `FONTS_DIR`/
  `FONT_FILE`/`font_available()`)
- `helpers/edl.py` (modified — `default_subtitle_font()`, dropped unused
  `import sys`)
- `tests/test_font_bundle.py` (new)
- `tests/test_edl.py` (modified — replaced one stale test with two, see
  above)

Committed as `8442f98` — `feat: bundle caption font instead of relying on
system Liberation Sans`. Staged explicitly (no `git add -A`/`.`/`-a`);
`git status --short` confirmed only the intended 6 paths were staged before
committing. The pre-existing unstaged modifications to `.superpowers/sdd/*.md`
(present before I started, per the initial git status in the environment
block) were left untouched and unstaged — not part of this task.

## Self-review

- **Does `font_available()` actually reflect reality?** Yes — it's a direct
  `FONT_FILE.is_file()` check against the real bundled path, and I confirmed
  the file is real (not an HTML 404 page, which is what the first download
  attempt silently produced) by inspecting its bytes and by successfully
  loading it with both Pillow and libass.
- **Does every caption style reference the bundled family?** Yes — all four
  entries in `CAPTION_STYLES` (`bold`, `neon`, `boxed`, `minimal`) use
  `"font": FONT`, verified by the passing
  `test_every_style_uses_the_bundled_family` test, and `FONT` is now the real
  `"DejaVu Sans"` rather than the Task-8 placeholder.
- **Is the `fonts_dir` wiring coherent with how it will be used at burn
  time?** `build_ass()`'s `fonts_dir` parameter remains accepted-but-unused
  inside the `.ass` file itself, which is correct and intentional — ASS files
  don't carry a fontsdir field; it's an ffmpeg/libass filter option
  (`ass=...:fontsdir=...`) applied by the *caller* at burn time. The
  foundation plan doc confirms this is deliberately deferred to the actual
  burn call site, which per the task constraints (`backend/render_engine.py`
  "must keep working until Task 12") is out of scope here. I validated the
  intended wiring works end-to-end by manually constructing the same
  `fontsdir=FONTS_DIR` call libass would receive and confirming resolution
  (see "beyond Step 6" above), so the pieces are proven compatible even
  though the actual call site isn't wired yet.

## Concerns

- Minor, harmless: when `fontsdir` points at `assets/fonts/`, libass attempts
  to load every file in that directory as a font candidate, including
  `README.md`, and logs `Error opening memory font 'README.md'`. This is
  cosmetic log noise only — verified it doesn't affect font resolution or
  cause any failure (exit code 0, correct font resolved). Not fixing since
  the brief specifies both files live in `assets/fonts/` and a later task
  owns the actual burn-time `fontsdir` wiring; flagging here in case a future
  task wants a fonts-only subdirectory to avoid the log noise.
- The brief's Step 1 download command (`curl` against the source repo's raw
  `master` branch) does not work as written — it 404s because that path
  doesn't exist in the source repo. Anyone re-running the brief literally
  will get a corrupt (HTML) "font" file with no error from `curl` unless they
  check the HTTP status or file contents. I did not modify the brief file
  itself (out of scope / not listed as a file to modify), but noting it here
  since it's a real trap for a literal re-run.

## Fix pass

A review found that the original Task 9 implementation achieved the opposite
of its stated purpose for the shipping code path: `helpers/render.py`'s
`subtitles=` filter (the actual burn call) never passed `fontsdir`, so
`default_subtitle_font()` requesting `"DejaVu Sans"` just made libass hit the
silent-substitution path harder, since (unlike `"Liberation Sans"`) that
family was never present on a stock Windows install either. This pass wires
`fontsdir` into the real burn call and fixes the `README.md` scan-noise
concern flagged in the original report's "Concerns" section.

### What changed

1. **`assets/fonts/ClipCutSans-Bold.ttf` → `assets/fonts/ttf/ClipCutSans-Bold.ttf`**
   (`git mv`, history preserved). `README.md` stays at `assets/fonts/README.md`,
   one directory up from the font file. This matters once `fontsdir` is a real
   ffmpeg option pointed at the font directory: libass tries to load *every*
   file in a `fontsdir` as a font candidate, so a non-font file living
   alongside the TTF produced a stray `Error opening memory font 'README.md'`
   log line on every burn (previously noted as cosmetic/deferred; now that the
   directory is actually consumed, it's cleaned up).

2. **`helpers/captions_ass.py`**: `FONTS_DIR` and `FONT_FILE` updated to point
   at the new `ttf/` subdirectory.

3. **`helpers/edl.py`**:
   - `default_subtitle_font()` now also gates on `captions_ass.font_available()`.
     It only returns `captions_ass.FONT` ("DejaVu Sans") when the bundled file
     is actually present; otherwise it returns `captions_ass.FONT_FALLBACK`
     ("Liberation Sans"). Previously it returned `captions_ass.FONT`
     unconditionally as long as the *import* succeeded, regardless of whether
     the file existed on disk — the actual bug this review caught.
   - New `default_subtitle_fontsdir() -> Path | None`: returns
     `captions_ass.FONTS_DIR` when the bundled font is present, `None`
     otherwise. Re-checks `font_available()` on every call (no caching), so
     it reflects the real filesystem state at call time — required for the
     negative-case test to work via monkeypatching `captions_ass.FONT_FILE`.

4. **`helpers/render.py`**: new `subtitles_filter_clause(subs_path, force_style_str)`
   builds the `subtitles='<path>'[:fontsdir='<dir>']:force_style='<style>'`
   clause, appending `:fontsdir=...` only when
   `edl.default_subtitle_fontsdir()` returns a real directory. The `fontsdir`
   value goes through `escape_subtitles_path()` — the same drive-colon/
   backslash escaping already used for the subtitles path itself, since an
   unescaped `D:\...` breaks ffmpeg filter parsing identically whether it's
   the subs path or the fontsdir value.
   `build_final_composite()`'s one subtitles-filter call site (previously
   inlining `f"subtitles='{subs_abs}':force_style='{force_style_str}'"`
   directly) now calls this function instead. Grepped the whole `helpers/`
   tree for other `subtitles=`/`force_style` builders first — this is the
   only call site; `helpers/captions_ass.py::build_ass()`'s `fonts_dir`
   parameter is accepted-but-unused by design (ASS files don't carry a
   fontsdir field; nothing in `helpers/` currently burns `.ass` output — that
   path lives only in `backend/render_engine.py`, out of scope per the
   "don't touch `render_engine.py`" constraint).

5. **Tests**:
   - `tests/test_font_bundle.py`: added
     `test_fonts_dir_is_a_fonts_only_subdirectory` (asserts `FONTS_DIR.name
     == "ttf"`, every entry under it has a `.ttf`/`.otf` suffix) and
     `test_readme_lives_outside_the_fonts_only_subdirectory`.
   - `tests/test_edl.py`: added
     `test_default_font_falls_back_when_bundled_file_missing`,
     `test_default_fontsdir_points_at_bundled_font_directory`,
     `test_default_fontsdir_none_when_bundled_file_missing`. The missing-file
     case is simulated by monkeypatching `captions_ass.FONT_FILE` to a
     nonexistent path inside `tmp_path` — the real bundled font is never
     deleted or touched.
   - `tests/test_render_subtitles.py` (new): asserts the clause
     `render.subtitles_filter_clause()` produces contains `fontsdir=` and
     `FontName=DejaVu Sans` when the bundled font is present, and — after the
     same `FONT_FILE` monkeypatch — omits `fontsdir=` entirely and contains
     `FontName=Liberation Sans` instead, with `captions_ass.FONT` absent from
     the clause altogether.

### Exact commands run

Run from the repo root:

```
.venv-local/Scripts/python.exe -m pytest tests/test_font_bundle.py tests/test_edl.py tests/test_render_subtitles.py -v
.venv-local/Scripts/python.exe -m pytest tests/ -q
```

Real burn (positive case — bundled font present), built from the exact
`render.subtitles_filter_clause()` output against a real `.srt` in the
scratchpad and a `-f lavfi testsrc` source:

```
ffmpeg -y -loglevel verbose -f lavfi -i testsrc=size=1280x720:duration=1:rate=1 \
  -vf "subtitles='<scratchpad>/burn_test.srt':fontsdir='D\:/Desktop/Desktop Files/Projects/clipcut/assets/fonts/ttf':force_style='FontName=DejaVu Sans,FontSize=18,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=90'" \
  -frames:v 1 burn_positive.png
```

Real burn (negative case — bundled font simulated absent by monkeypatching
`captions_ass.FONT_FILE` to a nonexistent path in a throwaway Python
process, never by deleting the real font):

```
ffmpeg -y -loglevel verbose -f lavfi -i testsrc=size=1280x720:duration=1:rate=1 \
  -vf "subtitles='<scratchpad>/burn_test.srt':force_style='FontName=Liberation Sans,FontSize=18,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=90'" \
  -frames:v 1 burn_negative.png
```

### Real fontselect output (positive case)

```
[Parsed_subtitles_0 @ 00000299215c0dc0] fontselect: (DejaVu Sans, 700, 0) -> DejaVuSans-Bold, 0, DejaVuSans-Bold
```

Exit code 0. Grepped the full stderr log for `memory font` / `readme`
(case-insensitive) — zero matches, confirming the `ttf/` subdirectory move
eliminated the `Error opening memory font 'README.md'` noise the original
report flagged.

### Negative-case result (bundled font simulated absent)

- `captions_ass.font_available()` → `False`
- `default_subtitle_font()` → `"Liberation Sans"` (the fallback family, not
  `"DejaVu Sans"`)
- `subtitles_filter_clause()` output contains no `fontsdir=` segment at all
- Real burn with that clause: exit code 0,
  `fontselect: (Liberation Sans, 700, 0) -> Arial-BoldMT, 0, Arial-BoldMT` —
  libass still substitutes here (Liberation Sans isn't on this Windows box
  either), but that's the same pre-existing, accepted fallback behavior the
  codebase already had for win32 before Task 9 (`"Arial"`), not a new
  problem: the fix's job was to stop requesting a family we know for certain
  isn't installed anywhere, which it does.

### Root-suite regression

Ran `pytest tests/ -q` from the repo root (matches the verified baseline
command). Result: **102 passed / 6 failed**, and the 6 failures are exactly
the pre-verified baseline set:

- `test_api.py::test_transcribe_requires_explicit_click`
- `test_api.py::test_transcribe_409_when_busy`
- `test_claude.py::test_first_turn_has_no_resume`
- `test_cut_picks.py::test_apply_cut_picks_zooms_and_drops`
- `test_talking_head.py::test_apply_bin_inserts_broll`
- `test_visual_picks.py::test_apply_visuals_inserts_broll_and_graphic`

(95 baseline passed + 7 new tests from this pass = 102; zero new failures,
zero fixed-by-accident.)

### Commit

`0482ccd` — `fix: wire fontsdir into the burn call so the bundled caption
font resolves`. Staged explicitly (`git add assets/fonts/README.md
assets/fonts/ttf/ClipCutSans-Bold.ttf helpers/captions_ass.py helpers/edl.py
helpers/render.py tests/test_edl.py tests/test_font_bundle.py
tests/test_render_subtitles.py`) — no `git add -A`/`.`/`-a`. The TTF move was
a `git mv`, so `git status`/the commit show it as a rename
(`assets/fonts/{ => ttf}/ClipCutSans-Bold.ttf`), preserving history. The
pre-existing unstaged modifications to other `.superpowers/sdd/*.md` files
(present before this pass started) were left untouched and unstaged, same as
the original Task 9 commit did.

### Concerns

None outstanding. The one concern flagged in the original report (README.md
log noise) is resolved by the `ttf/` subdirectory move and confirmed absent
in the real burn log above.
