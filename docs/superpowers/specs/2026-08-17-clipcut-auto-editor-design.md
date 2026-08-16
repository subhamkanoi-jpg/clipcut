# ClipCut auto-editor

Merge the two halves of this repo into one pipeline: a raw talking-head upload
goes in, a professionally edited reel comes out, with the user reviewing the AI's
plan before committing to a render.

## Problem

The repo contains two disconnected products.

`backend/` + `frontend/` is ClipCut: a working web app that transcribes, cuts on
speech, applies speech-driven zooms, burns karaoke captions, and reframes to 9:16.
It has a project library, chunked upload, and Cloudinary delivery.

`helpers/` is the `video-use` skill: an EDL-driven editor with b-roll fetching,
Ken Burns stills, graphics overlays, a 256-entry SFX catalog, timed audio mixing,
multi-source ranges, and two-pass loudness normalization.

`backend/` never imports `helpers/`. Each has its own renderer. Everything that
would make ClipCut feel like a professional edit already exists in `helpers/` and
is unreachable from the web app.

Three further defects block the merge:

- Export and transcription run in `threading.Thread` daemons
  (`backend/server.py:407`). A restart mid-export strands the project in
  `processing` forever. No cancel, no retry, no boot reconciliation.
- The plan is implicit. Cuts are recomputed from settings on every read; there is
  no persisted artifact describing the edit, so there is nothing to show a user
  for review and nothing to partially regenerate.
- `reframe.subject_center()` samples 14 frames and returns a single static x for
  the whole video. A speaker who moves drifts out of frame.

## Goals

- One renderer, one plan model, shared by the web app and the skill.
- Upload produces a reviewable edit plan: cuts, zooms, captions, b-roll, stills,
  keyword graphics.
- The user can toggle, lock, and swap any planned element, regenerate the
  unlocked remainder, then render.
- Jobs survive restarts, report progress, and can be cancelled.
- The whole pipeline runs with no LLM available, at reduced quality.

## Non-goals (v1)

Deferred to later sub-projects, in order:

- **Audio design** — SFX bed, music with speech ducking, voice cleanup
  (de-noise/EQ/compression). `helpers/voice_picks.py` and
  `helpers/sfx_library.py` already exist for this.
- **Transitions and slides** — cross-dissolves, wipes, full-screen title cards.
- **Editable transcript** — correcting Scribe mistakes before caption burn-in.
- **Subject tracking** — replacing the Haar cascade with `cv2.FaceDetectorYN` and
  per-frame smoothing.
- **Incremental re-render** — segment caching so a single swap does not re-render
  the whole timeline.

Also out of scope: authentication, multi-user, cloud hosting, and any 16:9-first
layout work. Output targets 9:16 with `original` retained as a passthrough.

## Users

One user, running on a Windows laptop, hosting locally. No concurrency
requirements beyond a single render at a time.

## Decisions already locked

From brainstorming on 2026-08-17:

1. **Creative intelligence comes from the Claude Code CLI as a subprocess.** Not
   the Anthropic API, not pure heuristics. Free with the existing subscription and
   fits local-only hosting. `app/server/claude.py` already proves the pattern.
2. **The user reviews the plan before rendering.** Not one-click-and-re-roll.
3. **v1 is the spine plus b-roll.** Spine-only was recommended; the user chose to
   fold b-roll in for visible payoff, accepting that b-roll is the element most
   likely to churn the plan model while it settles.
4. **One structured Claude call, not an agentic self-eval loop.** The backend
   writes briefs, invokes `claude` once, and reads back a JSON file. Quality
   ceiling is an editor working from a transcript; it never sees the footage.
5. **Mongo is the source of truth; `edit_dir` is derived.** The plan lives on the
   project document. `data/<pid>/edit/` is materialized for `helpers/` to consume
   and can be deleted and rebuilt at any time.
6. **Integration is in-process import via `sys.path`.** Verified safe: `helpers/`
   modules cross-import flatly (`from edl import ...`), and there are zero
   filename collisions between `backend/*.py` and `helpers/*.py`.

## Architecture

Three processes:

- **API** (`backend/server.py`, uvicorn) — HTTP, Mongo reads/writes, enqueues
  jobs. Never runs ffmpeg or Claude.
- **Worker** (`backend/worker.py`, new) — claims jobs, runs transcription,
  planning, and rendering. Inserts `helpers/` on `sys.path` at startup, the same
  way `app/server/jobs.py:17` does.
- **Mongo** — projects, plans, and the job queue.

Moving all long work out of the API process is what makes cancellation and
restart-safety possible; it is not an optimization.

### Layout

```
backend/
  server.py            API only; job enqueue replaces threading.Thread
  worker.py            NEW  claim loop, lease/heartbeat, stage dispatch
  jobs.py              NEW  queue primitives over Mongo
  plan/
    __init__.py        NEW
    model.py           NEW  EDL v2 dataclasses + validation
    assemble.py        NEW  cuts + picks -> EDL v2
    materialize.py     NEW  Mongo plan <-> data/<pid>/edit/ on disk
    providers/
      base.py          NEW  DecisionProvider protocol
      claude_cli.py    NEW  brief -> claude -> picks.json
      heuristic.py     NEW  keyword/catalog matching, no LLM
  render_engine.py     DELETED after parity test
  reframe.py           unchanged in v1; subject_center feeds reframe.center_x
helpers/
  render.py            gains center_x cover mode; single renderer
  captions_ass.py      NEW  karaoke ASS, ported from render_engine.build_ass
  edl.py               validate_edl extended for v2
frontend/src/screens/
  PlanReview.jsx       NEW  three-lane timeline, the review gate
```

## Data flow

```
upload (chunked, existing)
  -> job: transcribe        Scribe, existing transcription.py
  -> job: plan
       heuristic cuts (backend/cuts.py, existing)
       materialize edit_dir
       write brief.md
       DecisionProvider.plan() -> picks
       assemble EDL v2 -> Mongo
  -> PLAN REVIEW  (user toggles / locks / swaps / regenerates)
  -> job: render
       fetch b-roll + stills
         downloads land in data/<pid>/edit/bin/broll/ (apply_visuals' dest_dir)
         library/broll/ is visual_picks' shared cache, checked first
       extract segments with zoom + center_x crop
       concat
       composite overlays
       burn karaoke ASS
       two-pass loudnorm
  -> export.mp4  (+ optional Cloudinary upload, existing)
```

Regeneration re-runs only the `plan` job, preserving every element marked
`locked`. Rendering is always full; incremental re-render is a non-goal.

## Contracts

### EDL v2

Extends the v1 in `SKILL.md:266`. New: `reframe`, `captions`, `audio_overlays` as
a first-class list, and per-overlay `enabled` / `locked` / provenance.

```json
{
  "version": 2,
  "project_id": "uuid",
  "sources": { "main": "data/<pid>/source.mp4" },
  "ranges": [
    { "source": "main", "start": 2.42, "end": 6.85,
      "zoom": 1.06, "variation": "push",
      "beat": "HOOK", "reason": "cleanest delivery" }
  ],
  "reframe": { "aspect": "9:16", "center_x": 0.42 },
  "captions": { "style": "neon", "karaoke": true, "burn": true },
  "overlays": [
    { "id": "ov1", "kind": "broll",
      "start_in_output": 3.2, "duration": 2.4,
      "file": "library/broll/typing-on-laptop.mp4",
      "query": "typing on laptop", "source": "mixkit",
      "enabled": true, "locked": false }
  ],
  "audio_overlays": [],
  "grade": "none",
  "total_duration_s": 15.07
}
```

`enabled` hides an element without losing it. `locked` protects it from
regeneration. `query` and `source` let the UI offer a swap. `audio_overlays` is
present but always empty in v1; it exists so the audio sub-project does not
require a schema migration.

`reframe.center_x` is a single value in v1, matching today's behaviour. The
tracking sub-project replaces it with a keyframe list; readers must accept both.

### `picks.json`

Written by Claude to `data/<pid>/edit/picks.json`. Validated against a schema;
any failure discards the whole file and falls back to the heuristic provider.

```json
{
  "cuts":     [ { "range_i": 0, "variation": "push", "score": 0.8 } ],
  "visuals":  [ { "kind": "broll", "after_i": 2, "query": "typing on laptop",
                  "duration_s": 2.4 } ],
  "graphics": [ { "text": "NEVER", "start_s": 1.1, "duration_s": 1.6 } ]
}
```

Shapes deliberately mirror what `helpers/visual_picks.apply_visuals` and
`helpers/cut_picks.apply_cut_picks` already consume.

### Claude invocation

One-shot, non-interactive, cwd set to the project's `edit_dir`:

- Timeout 180s, process killed on expiry.
- Success is `picks.json` existing and validating — stdout is not parsed.
- Any failure (binary missing, timeout, invalid JSON) logs and falls back to
  `HeuristicProvider`. Planning never hard-fails.

### Jobs collection

```json
{ "id": "uuid", "project_id": "uuid",
  "kind": "transcribe | plan | render",
  "status": "queued | processing | done | error | cancelled",
  "stage": "fetching", "progress": 42,
  "attempts": 1, "max_attempts": 3,
  "lease_expires_at": "iso", "heartbeat_at": "iso",
  "cancel_requested": false, "error": null }
```

Claim is a single `find_one_and_update` on `status: queued`, setting
`processing` and a lease. The worker heartbeats every 5s. On boot it requeues any
`processing` job whose lease has expired, incrementing `attempts` and failing past
`max_attempts`.

### API changes

- `POST /api/projects/{pid}/plan` — enqueue planning. Body: `{ regenerate: bool }`.
- `GET  /api/projects/{pid}/plan` — current EDL v2.
- `PATCH /api/projects/{pid}/plan/overlays/{oid}` — `enabled` and `locked` are
  metadata-only and apply immediately. Changing `query` clears the resolved
  `file` and marks the overlay unresolved; the next render re-fetches it. The
  PATCH itself never touches the network, so the UI stays responsive.
- `POST /api/projects/{pid}/export` — unchanged shape, now enqueues a job.
- `POST /api/jobs/{jid}/cancel` — sets `cancel_requested`.
- `GET  /api/jobs/{jid}` — status, stage, progress.

## Renderer convergence

`helpers/render.py` becomes the only renderer. It already has the harder
capabilities: overlay compositing with PTS shifting (`build_final_composite`),
timed audio mixing (`mix_audio_overlays`), multi-source ranges, two-pass
loudnorm, and HDR tonemapping.

Two ports from `backend/render_engine.py`:

1. **Face-centered crop.** `extract_segment`'s `cover` mode hardcodes
   `crop=1080:1920` (centered). Add a `center_x: float = 0.5` parameter and
   compute the crop x-offset from it, reusing the logic in
   `render_engine.crop_spec`.
2. **Karaoke captions.** `render_engine.build_ass` produces word-by-word ASS with
   inline overrides. Move it to `helpers/captions_ass.py`. `build_final_composite`
   chooses ASS when `captions.karaoke` is true, else the existing SRT path.

`CAPTION_STYLES` moves with it, and the hardcoded `FONT = "Liberation Sans"` is
replaced by a bundled font file with an explicit `fontsdir`, since libass
substitutes silently rather than erroring.

`backend/render_engine.py` is deleted only after a parity test renders the same
source through both paths and asserts matching dimensions, duration, and frame
count.

## Plan review UI

A screen between the transcript and the export, reached when a plan job
completes. Three stacked lanes over a shared time axis:

- **Ranges** — kept segments, with zoom markers and the cut reason on hover.
- **Overlays** — b-roll, stills, keyword graphics as blocks.
- **Audio** — empty in v1, rendered so the layout does not change later.

Per element: enable/disable, lock, and for b-roll a swap that re-queries with
different text. A "Regenerate unlocked" action re-runs planning. "Render"
commits.

The existing `TranscriptPanel` and cut-toggle behaviour are unchanged; plan review
is additive.

## Error handling

- **Asset fetch fails** (Mixkit or Pexels down, no network) — `apply_visuals`
  already substitutes a keyword graphic. Preserved, and surfaced in the UI as a
  degraded element rather than silently.
- **Claude fails** — fall back to `HeuristicProvider`, record which provider ran
  on the plan so the UI can say so.
- **ffmpeg fails** — job goes to `error` with the last 2KB of stderr. Never leave
  a project in `processing`.
- **Worker dies** — lease expiry requeues on next boot.
- **Invalid EDL** — `validate_edl` runs before any render; failures are a job
  error, not a crash.
- **Cancellation** — checked between stages and before each ffmpeg spawn; the
  child is killed and partial outputs deleted.

## Testing

The existing suite covers the helpers half (`tests/`, 16 files including
`test_edl.py`, `test_visual_picks.py`, `test_cut_picks.py`). Backend has
`backend/tests/test_backend_e2e.py` and `test_reel_backend.py`.

New coverage:

- **Unit** — EDL v2 validation including the v1-compatibility path; plan assembly
  from cuts + picks; `picks.json` schema rejection; lock-preserving regeneration;
  queue claim atomicity and lease expiry.
- **Integration** — full pipeline on a short fixture clip using
  `HeuristicProvider`, asserting output dimensions, duration, and overlay count.
  This runs without Claude, so it works in CI and offline.
- **Parity** — the render_engine-vs-helpers comparison gating the deletion.
- **Manual** — one real Claude-backed plan reviewed by eye before the sub-project
  is called done.

Network-dependent asset fetching is stubbed in tests; the fixture ships with one
small local b-roll clip.

## Operations

Two processes now instead of one:

```
backend:  uvicorn server:app --port 8000     (cwd backend/)
worker:   python worker.py                   (cwd backend/)
frontend: npm start
```

A `scripts/dev.ps1` starts all three. The worker is safe to restart at any time;
in-flight jobs requeue.

## Implementation order

1. Job queue and worker; migrate transcription and export off daemon threads with
   no behaviour change.
2. EDL v2 model, validation, materialize/dematerialize.
3. Renderer convergence and the parity test; delete `render_engine.py`.
4. `HeuristicProvider` and plan assembly, end to end.
5. Plan review UI against heuristic plans.
6. `ClaudeCliProvider` and the brief.
7. B-roll and stills through `visual_picks`, including the fetch-failure path.

Steps 1-3 are invisible to the user, which is the cost of the foundation. Step 7
is where it starts looking like a professional edit.

## Open items explicitly closed

- **Redis vs Mongo for the queue** — Mongo. No second service on a laptop.
- **16:9 output** — not in v1. `original` stays a passthrough.
- **Incremental re-render** — not in v1. Every render is full.
- **"Voice effects"** — read as sound effects, deferred to the audio
  sub-project. If the user meant voice processing, that is a separate feature and
  does not change this design.
- **Agentic Claude** — rejected for v1; the upgrade path is an optional deep pass
  behind the same `DecisionProvider` interface.
