### Task 1: EDL v2 validation accepts the v1-compat shape

**Files:**
- Modify: `backend/plan/model.py` (the `validate` function)
- Test: `backend/tests/test_plan_model.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `validate(plan)` accepts `version` 1 or 2, and `reframe.center_x` as either a scalar in `[0,1]` or a keyframe list `[{"t": float, "cx": float}, ...]`.

This closes the whole-branch review's plan-2 carry-forward: the spec requires
"EDL v2 validation including the v1-compatibility path", and readers must accept
both the scalar `center_x` and a future keyframe track.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_plan_model.py`:

```python
def test_validate_accepts_version_1():
    p = model.new_plan("p1", "s.mp4")
    p["version"] = 1
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    assert model.validate(p) == []


def test_validate_accepts_center_x_keyframe_list():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    p["reframe"]["center_x"] = [{"t": 0.0, "cx": 0.4}, {"t": 1.5, "cx": 0.6}]
    assert model.validate(p) == []


def test_validate_rejects_keyframe_with_bad_cx():
    p = model.new_plan("p1", "s.mp4")
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    p["reframe"]["center_x"] = [{"t": 0.0, "cx": 1.9}]
    assert any("center_x" in e for e in model.validate(p))


def test_validate_still_rejects_version_3():
    p = model.new_plan("p1", "s.mp4")
    p["version"] = 3
    p["ranges"] = [{"source": "main", "start": 0.0, "end": 2.0}]
    assert any("version" in e for e in model.validate(p))
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_plan_model.py -v`
Expected: the four new tests FAIL (version-1 rejected; keyframe list rejected as non-numeric center_x).

- [ ] **Step 3: Implement**

In `backend/plan/model.py`, replace the version check and the `center_x` check in `validate`:

```python
    if plan.get("version") not in (1, PLAN_VERSION):
        errors.append(f"version must be 1 or {PLAN_VERSION}, got {plan.get('version')!r}")
```

```python
    cx = reframe.get("center_x", 0.5)
    if isinstance(cx, list):
        for j, kf in enumerate(cx):
            if not isinstance(kf, dict):
                errors.append(f"reframe.center_x[{j}] must be an object")
                continue
            try:
                t = float(kf["t"])
                c = float(kf["cx"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"reframe.center_x[{j}] needs numeric t and cx")
                continue
            if t < 0:
                errors.append(f"reframe.center_x[{j}].t must be >= 0")
            if not 0.0 <= c <= 1.0:
                errors.append(f"reframe.center_x[{j}].cx must be in [0, 1], got {c}")
    else:
        try:
            if not 0.0 <= float(cx) <= 1.0:
                errors.append(f"reframe.center_x must be in [0, 1], got {cx}")
        except (TypeError, ValueError):
            errors.append(f"reframe.center_x must be numeric, got {cx!r}")
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv-local/Scripts/python.exe -m pytest tests/test_plan_model.py -v`
Expected: PASS (all, including the pre-existing ones).

- [ ] **Step 5: Commit**

```bash
git add backend/plan/model.py backend/tests/test_plan_model.py
git commit -m "feat: EDL v2 validation accepts v1 version and center_x keyframes"
```

---

