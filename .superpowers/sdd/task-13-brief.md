### Task 13: Dev script for the three processes

**Files:**
- Create: `scripts/dev.ps1`
- Modify: `README.md` (add a ClipCut section)

**Interfaces:**
- Consumes: nothing.
- Produces: a single command that starts API, worker, and frontend.

- [ ] **Step 1: Write the script**

Create `scripts/dev.ps1`:

```powershell
# Start ClipCut: API, worker, and frontend.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv-local\Scripts\python.exe"

if (-not (Test-Path $py)) { throw "venv missing at $py" }

$svc = Get-Service -Name MongoDB -ErrorAction SilentlyContinue
if ($null -eq $svc -or $svc.Status -ne "Running") {
    throw "MongoDB service is not running. Start it, then retry."
}

Start-Process -FilePath $py -ArgumentList "-m","uvicorn","server:app","--port","8000" `
              -WorkingDirectory (Join-Path $root "backend")
Start-Process -FilePath $py -ArgumentList "worker.py" `
              -WorkingDirectory (Join-Path $root "backend")
Start-Process -FilePath "npm" -ArgumentList "start" `
              -WorkingDirectory (Join-Path $root "frontend")

Write-Output "API      http://localhost:8000"
Write-Output "Frontend http://localhost:3000"
Write-Output "Worker   running (check its window for job logs)"
```

- [ ] **Step 2: Run it**

Run: `powershell -ExecutionPolicy Bypass -File scripts\dev.ps1`
Expected: three windows open; `curl http://localhost:8000/api/` returns
`{"status":"ok"}`.

- [ ] **Step 3: Document it**

Add to `README.md` after the "Local app (Windows)" section:

```markdown
### ClipCut (web reel editor)

ClipCut is the browser-based reel editor in `backend/` + `frontend/`. It needs
MongoDB running locally and an ElevenLabs key in `backend/.env`.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

This starts three processes: the API on :8000, the job worker, and the frontend
on :3000. The worker runs all transcription and rendering; the API only enqueues.

Backend dependencies are the eight packages the code actually imports — do not
install `backend/requirements.txt`, which is a stale Emergent lockfile pinning a
private wheel.
```

- [ ] **Step 4: Commit**

```bash
git add scripts/dev.ps1 README.md
git commit -m "docs: add ClipCut dev script and README section"
```

---

## Self-Review

**Spec coverage.** Every spec section for steps 1-3 maps to a task: job queue
(Tasks 1-2), transcription and export migration (3-4), EDL v2 (5), materialize
(6), renderer convergence (7-9, 11-12), assembly (10), operations (13). The
`GET /api/jobs/{jid}` and cancel routes land in Task 4. Deferred to plan 2, as
the spec states: decision providers, `POST /api/projects/{pid}/plan`, the
overlay PATCH route, plan-review UI, and b-roll.

**Known gaps carried into plan 2.** `plan.assemble.from_project` always emits
empty `overlays`, and `render_plan._composite` filters on `enabled` but has
nothing to filter yet. Both are intentional: the schema and the code path exist
so plan 2 adds data, not structure.

**Type consistency.** `Ctx.progress(p, stage)` is two-positional throughout;
Task 4's initial `cb(p)` is explicitly widened to `cb(p, stage)` in Task 12
Step 4 when the renderer changes. `model.validate` returns `list[str]`
everywhere. `cover_crop_filter` is used only by `extract_segment`. `CAPTION_STYLES`
moves from `render_engine` to `captions_ass` in Task 8 and every later reference
uses the new home.

**Risk.** Task 12 is the only destructive task and is gated on Task 12 Step 3
passing. If parity fails, stop and reconcile rather than deleting.
