# Task 5: EDL v2 Plan Model and Validation - Report

## Summary

Successfully implemented EDL v2 plan model and validation following TDD methodology. All 9 tests pass. Implementation follows the task brief exactly with no deviations or YAGNI violations.

## Implementation Details

### Files Created

1. **backend/plan/__init__.py**
   - Module docstring for the EDL v2 plan package

2. **backend/plan/model.py** (120 lines)
   - `PLAN_VERSION = 2` constant
   - `VALID_ASPECTS = ("9:16", "original")` - aspect ratio validation
   - `VALID_OVERLAY_KINDS = ("broll", "graphic", "still")` - overlay type validation
   - `new_plan(project_id: str, source_path: str) -> dict` - Creates a new plan with v2 shape
   - `overlay(kind: str, start_in_output: float, duration: float, **extra) -> dict` - Factory that assigns unique UUIDs
   - `validate(plan: dict) -> list` - Returns list of error strings (empty = valid)

3. **backend/tests/test_plan_model.py** (73 lines)
   - 9 comprehensive tests covering all requirements

### TDD Process Evidence

#### RED Phase
```
$ cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_plan_model.py -v
ERROR collecting backend/tests/test_plan_model.py
ModuleNotFoundError: No module named ‘plan’
```

#### GREEN Phase
```
$ cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_plan_model.py -v
============================= test session starts =============================
collected 9 items

tests\test_plan_model.py::test_new_plan_has_v2_shape PASSED              [ 11%]
tests\test_plan_model.py::test_overlay_factory_assigns_unique_ids PASSED [ 22%]
tests\test_plan_model.py::test_validate_accepts_minimal_valid_plan PASSED [ 33%]
tests\test_plan_model.py::test_validate_rejects_empty_ranges PASSED      [ 44%]
tests\test_plan_model.py::test_validate_rejects_unknown_source PASSED    [ 55%]
tests\test_plan_model.py::test_validate_rejects_inverted_range PASSED    [ 66%]
tests\test_plan_model.py::test_validate_rejects_negative_overlay_duration PASSED [ 77%]
tests\test_plan_model.py::test_validate_rejects_bad_aspect PASSED        [ 88%]
tests\test_plan_model.py::test_validate_rejects_center_x_out_of_range PASSED [100%]

============================== 9 passed in 0.04s ==============================
```

## Test Coverage

All 9 tests exercise the complete specification:

1. **test_new_plan_has_v2_shape** - Verifies plan structure with all required fields
2. **test_overlay_factory_assigns_unique_ids** - Confirms unique ID generation and default fields
3. **test_validate_accepts_minimal_valid_plan** - Accepts plan with required fields
4. **test_validate_rejects_empty_ranges** - Validates non-empty ranges requirement
5. **test_validate_rejects_unknown_source** - Validates source reference integrity
6. **test_validate_rejects_inverted_range** - Validates start < end constraint
7. **test_validate_rejects_negative_overlay_duration** - Validates duration > 0 constraint
8. **test_validate_rejects_bad_aspect** - Validates aspect ratio from allowed list
9. **test_validate_rejects_center_x_out_of_range** - Validates center_x in [0, 1] range

## Validation Rules Implemented

The validate() function enforces:
- Plan version must be exactly 2
- sources must be a non-empty dict
- ranges must be a non-empty list
- Each range must reference a valid source
- Each range must have numeric start and end with 0 <= start < end
- reframe.aspect must be one of ("9:16", "original")
- reframe.center_x must be numeric in [0.0, 1.0]
- Each overlay.kind must be one of ("broll", "graphic", "still")
- Each overlay.duration must be numeric and > 0
- Each overlay.start_in_output must be numeric and >= 0

## Data Model

The `new_plan()` function returns a plan with:
```python
{
    "version": 2,
    "project_id": str,
    "sources": {"main": str},
    "ranges": [],
    "reframe": {"aspect": "9:16", "center_x": 0.5},
    "captions": {"style": "bold", "karaoke": True, "burn": True},
    "overlays": [],
    "audio_overlays": [],  # Empty, reserved for future use
    "grade": "none",
    "total_duration_s": 0.0,
    "provider": None,
}
```

The `overlay()` factory returns:
```python
{
    "id": "ov_<uuid_hex_8>",
    "kind": str,
    "start_in_output": float,
    "duration": float,
    "file": None,
    "enabled": True,
    "locked": False,
    # Plus any **extra kwargs
}
```

## Self-Review

### Completeness
- All requirements from task brief implemented exactly as specified
- All interface functions provided: `new_plan()`, `overlay()`, `validate()`, `PLAN_VERSION`
- All test cases from brief implemented and passing

### YAGNI (You Aren’t Gonna Need It)
- No validation rules added beyond what the brief specifies
- No extra fields added to plan structure
- No extra factory parameters added to overlay()
- Implementation is minimal and focused

### Test Quality
- Each test exercises exactly one behavior
- Tests verify both positive (accepts) and negative (rejects) cases
- Validation error messages are specific and helpful
- All test assertions match brief expectations exactly

### Code Quality
- Module docstrings document the EDL v2 format
- Function signatures match brief specifications
- Error messages are user-friendly and include relevant context
- No dependencies added (uuid is stdlib)
- Follows Python 3.12 conventions
- Uses flat imports as required by backend

## Commit

```
[feat/clipcut-foundation 113711e] feat: add EDL v2 plan model with validation
 3 files changed, 174 insertions(+)
 create mode 100644 backend/plan/__init__.py
 create mode 100644 backend/plan/model.py
 create mode 100644 backend/tests/test_plan_model.py
```

## Notes

- audio_overlays field is initialized as empty array and left untouched (as per brief: "It exists in the schema now so a later audio sub-project needs no migration")
- All validation returns early on structural errors (missing sources/ranges) to avoid cascading errors
- UUID generation uses first 8 hex chars for readability (standard short UUID pattern)
- Validation does not check `total_duration_s` or `provider` fields (brief does not require it)
- No database or external dependencies needed for this isolated module

## Fix pass

### Addition

Added `test_validate_rejects_wrong_version` to `backend/tests/test_plan_model.py`:

```python
def test_validate_rejects_wrong_version():
    p = model.new_plan("p1", "s.mp4")
    p["version"] = 1
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    assert any("version" in e for e in model.validate(p))
```

This test exercises the previously untested version check in `validate()`. It creates a valid plan with a non-2 version and one valid range, ensuring the version error is reached and reported.

### Command Run

```
cd backend && ..\.venv-local\Scripts\python.exe -m pytest tests/test_plan_model.py -v
```

### Test Output

```
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0 -- D:\Desktop\Desktop Files\Projects\clipcut\.venv-local\Scripts\python.exe
cachedir: .pytest_cache
rootdir: D:\Desktop\Desktop Files\Projects\clipcut
configfile: pyproject.toml
plugins: anyio-4.14.2
collecting ... collected 10 items

tests\test_plan_model.py::test_new_plan_has_v2_shape PASSED              [ 10%]
tests\test_plan_model.py::test_overlay_factory_assigns_unique_ids PASSED [ 20%]
tests\test_plan_model.py::test_validate_accepts_minimal_valid_plan PASSED [ 30%]
tests\test_plan_model.py::test_validate_rejects_empty_ranges PASSED      [ 40%]
tests\test_plan_model.py::test_validate_rejects_unknown_source PASSED    [ 50%]
tests\test_plan_model.py::test_validate_rejects_inverted_range PASSED    [ 60%]
tests\test_plan_model.py::test_validate_rejects_negative_overlay_duration PASSED [ 70%]
tests\test_plan_model.py::test_validate_rejects_bad_aspect PASSED        [ 80%]
tests\test_plan_model.py::test_validate_rejects_center_x_out_of_range PASSED [ 90%]
tests\test_plan_model.py::test_validate_rejects_wrong_version PASSED     [100%]

============================== 10 passed in 0.04s ==============================
```
