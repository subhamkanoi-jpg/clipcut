# Task 10 Report: Build an EDL v2 from Current Project State

## Status

**DONE.** Branch eat/clipcut-foundation. Implemented and committed.

## Summary

Successfully implemented ackend/plan/assemble.py with the rom_project() function that transforms a project document and computed cut state into a validated EDL v2 plan. All 8 required tests pass; zero regressions across the entire backend suite (48/48 passing).

## What Shipped

| File | Role |
|------|------|
| ackend/plan/assemble.py | rom_project(doc: dict, cut_state: dict) -> dict - assembles EDL v2 from project + cuts |
| ackend/tests/test_assemble.py | 8 test cases covering all mappings from project state to plan |

## Implementation Details

The rom_project() function:

1. **Initializes the plan** using model.new_plan() with project ID and source path
2. **Maps keep_ranges to ranges array**: Iterates over cut_state["keep_ranges"] (tuples), converts each (start, end) pair to a dict with source="main", zoom=1.0, accumulates total duration
3. **Extracts caption settings** from doc.get("caption_style") and eel.get("karaoke") / eel.get("burn_captions")
4. **Builds reframe settings** from eel.get("aspect") and doc.get("subject_center_x", 0.5) with proper float conversion
5. **Returns validated plan** that passes model.validate() with zero errors

## Key Mappings

| Plan Field | Source | Default | Notes |
|------------|--------|---------|-------|
| ranges | cut_state.keep_ranges | [] | Each tuple → dict with source="main" |
| total_duration_s | Sum of range durations | 0.0 | Rounded to 3 decimals |
| captions.style | doc.caption_style | "bold" | Direct pass-through |
| captions.karaoke | reel.karaoke | True | Boolean coercion |
| captions.burn | reel.burn_captions | True | Boolean coercion |
| reframe.aspect | reel.aspect | "9:16" | Supports "9:16" and "original" |
| reframe.center_x | doc.subject_center_x | 0.5 | Float in [0, 1] |

## TDD Progression

### Step 1: Write Failing Tests
Created test_assemble.py with 8 test cases covering:
- Range mirroring from keep_ranges (count, start, end, source)
- Caption extraction from project settings (style, karaoke, burn)
- Reframe aspect from reel_settings
- Aspect passthrough for "original"
- Total duration calculation
- Output validation via model.validate()
- center_x defaults and overrides

### Step 2: RED — Verify Failure
`
ERROR tests/test_assemble.py:6: in <module>
    from plan import assemble, model
E   ImportError: cannot import name 'assemble' from 'plan'
`
Expected failure — module does not exist yet.

### Step 3: Write Minimal Implementation
Created assemble.py with exact implementation from task brief (30 lines):
- No extra fields beyond what tests require
- No overlay population (brief explicitly states this is Task 11)
- Clean, dependency-free: only imports plan.model
- Flat imports (not package-relative) per project conventions

### Step 4: GREEN — Verify Success
`
============================= 8 passed in 0.04s ==============================
tests/test_assemble.py::test_ranges_mirror_keep_ranges PASSED            [ 12%]
tests/test_assemble.py::test_captions_come_from_project_settings PASSED  [ 25%]
tests/test_assemble.py::test_reframe_aspect_comes_from_reel_settings PASSED [ 37%]
tests/test_assemble.py::test_original_aspect_is_preserved PASSED         [ 50%]
tests/test_assemble.py::test_total_duration_is_the_sum_of_ranges PASSED  [ 62%]
tests/test_assemble.py::test_output_validates PASSED                     [ 75%]
tests/test_assemble.py::test_center_x_defaults_to_half_when_absent PASSED [ 87%]
tests/test_assemble.py::test_center_x_is_carried_through_when_present PASSED [100%]
`

All tests pass.

### Step 5: Regression Testing
Ran full backend suite excluding live e2e tests:
`
============================= 48 passed in 3.85s ==============================
`

40 existing tests (all passing) + 8 new tests = 48 total. Zero regressions.

## Test Coverage Analysis

Each test pins a specific mapping from project state to plan fields:

1. test_ranges_mirror_keep_ranges: Verifies tuple→dict conversion and source="main" assignment
2. test_captions_come_from_project_settings: Confirms caption_style, karaoke, burn extraction
3. test_reframe_aspect_comes_from_reel_settings: Aspect from reel settings
4. test_original_aspect_is_preserved: "original" aspect passthrough (not just "9:16")
5. test_total_duration_is_the_sum_of_ranges: Duration accumulation logic with floating-point tolerance
6. test_output_validates: End-to-end validation against model schema (catches missing/malformed fields)
7. test_center_x_defaults_to_half_when_absent: Default value behavior
8. test_center_x_is_carried_through_when_present: Override from subject_center_x

## YAGNI Review

- No overlay population: Brief explicitly states "overlays stays empty here. Populating it is a later sub-project." plan["overlays"] inherited from model.new_plan() as empty list, never modified.
- No extra fields: Implementation adds only ranges, total_duration_s, reframe, captions. All other fields come from model.new_plan() template.
- No unused dependencies: Only imports plan.model.
- Flat imports: Follows project convention (not package-relative).

## Completeness Checklist

- [x] Consumes plan.model.new_plan(), plan.model.validate()
- [x] Consumes cut_state.keep_ranges (tuples, not dicts)
- [x] Maps caption_style to captions.style
- [x] Maps reel_settings.karaoke to captions.karaoke
- [x] Maps reel_settings.burn_captions to captions.burn
- [x] Maps reel_settings.aspect to reframe.aspect (preserves "original")
- [x] Maps subject_center_x to reframe.center_x (defaults to 0.5)
- [x] Computes total_duration_s as sum of range durations
- [x] Returns validated plan (passes model.validate())
- [x] Leaves overlays empty (no population)
- [x] All 8 tests pass
- [x] Zero backend regressions
- [x] Committed with explicit paths only (no git add -A)

## Commit

`
bf2440e feat: assemble EDL v2 from project state and cuts
`

Files: backend/plan/assemble.py, backend/tests/test_assemble.py

## Concerns

None. The implementation is complete, minimal, well-tested, and ready for Task 11 (Plan Materialization).
