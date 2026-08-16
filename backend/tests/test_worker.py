import logging
import os
import queue
import subprocess
import sys
import threading
import time

import pytest
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs as jobs_mod
import worker as worker_mod

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def db():
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    name = "clipcut_test"
    client.drop_database(name)
    yield client[name]
    client.drop_database(name)


def test_run_once_returns_false_when_queue_empty(db):
    assert worker_mod.run_once(db, "w1") is False


def test_run_once_dispatches_and_finishes(db):
    seen = {}

    def handler(ctx):
        seen["project_id"] = ctx.project_id
        ctx.progress(50, "working")
        return {"ok": True}

    worker_mod.HANDLERS["dummy"] = handler
    try:
        jid = jobs_mod.enqueue(db, "proj1", "dummy")
        assert worker_mod.run_once(db, "w1") is True
        doc = db.jobs.find_one({"id": jid})
        assert doc["status"] == "done"
        assert doc["result"] == {"ok": True}
        assert seen["project_id"] == "proj1"
    finally:
        del worker_mod.HANDLERS["dummy"]


def test_handler_exception_fails_the_job(db):
    def handler(ctx):
        raise RuntimeError("kaboom")

    worker_mod.HANDLERS["dummy"] = handler
    try:
        jid = jobs_mod.enqueue(db, "proj1", "dummy")
        worker_mod.run_once(db, "w1")
        doc = db.jobs.find_one({"id": jid})
        assert doc["status"] == "error"
        assert "kaboom" in doc["error"]
    finally:
        del worker_mod.HANDLERS["dummy"]


def test_unknown_kind_fails_cleanly(db):
    jid = jobs_mod.enqueue(db, "proj1", "no-such-kind")
    worker_mod.run_once(db, "w1")
    doc = db.jobs.find_one({"id": jid})
    assert doc["status"] == "error"
    assert "no handler" in doc["error"]


def test_ctx_cancelled_reflects_flag(db):
    observed = {}

    def handler(ctx):
        observed["before"] = ctx.cancelled()
        jobs_mod.request_cancel(ctx.db, ctx.job["id"])
        observed["after"] = ctx.cancelled()
        return {}

    worker_mod.HANDLERS["dummy"] = handler
    try:
        jobs_mod.enqueue(db, "proj1", "dummy")
        worker_mod.run_once(db, "w1")
        assert observed == {"before": False, "after": True}
    finally:
        del worker_mod.HANDLERS["dummy"]


def test_handler_raising_cancelled_marks_job_cancelled(db):
    def handler(ctx):
        raise worker_mod.Cancelled()

    worker_mod.HANDLERS["dummy"] = handler
    try:
        jid = jobs_mod.enqueue(db, "proj1", "dummy")
        worker_mod.run_once(db, "w1")
        doc = db.jobs.find_one({"id": jid})
        assert doc["status"] == "cancelled"
        assert doc["stage"] == "cancelled"
        assert doc["finished_at"] is not None
    finally:
        del worker_mod.HANDLERS["dummy"]


def test_progress_warns_when_lease_lost(db, caplog):
    jid = jobs_mod.enqueue(db, "proj1", "render")
    job = jobs_mod.claim(db, ["render"], "worker-a")
    # Simulate another worker reclaiming/finishing the job so our lease is lost.
    db.jobs.update_one({"id": jid}, {"$set": {"status": "queued"}})

    ctx = worker_mod.Ctx(db=db, job=job, project_id=job["project_id"], payload={})
    with caplog.at_level(logging.WARNING, logger="worker"):
        ctx.progress(10, "working")

    assert f"lost lease on job {jid}" in caplog.text


def _pump_lines(stream, q):
    for line in iter(stream.readline, ""):
        q.put(line)
    q.put(None)


def test_running_as_script_registers_all_handlers():
    # Regression test: worker.py is started with `python worker.py`, which
    # makes its module name "__main__". Handler modules do `import worker`,
    # which (if the __main__ block just calls main() directly) creates a
    # SECOND, separate module object under the name "worker" -- with its own
    # empty HANDLERS dict and its own Cancelled class. Handlers register into
    # that second copy while main() reads the first copy's HANDLERS, which
    # stays empty forever. This test launches worker.py exactly the way a
    # user/deployment does -- as a real subprocess -- and checks that the
    # startup log line actually lists the registered handlers.
    proc = subprocess.Popen(
        [sys.executable, "worker.py"],
        cwd=BACKEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    out_q: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_pump_lines, args=(proc.stderr, out_q), daemon=True)
    reader.start()
    try:
        deadline = time.time() + 30
        ready_line = None
        while time.time() < deadline:
            remaining = deadline - time.time()
            try:
                line = out_q.get(timeout=max(0.1, remaining))
            except queue.Empty:
                break
            if line is None:
                break
            if "ready, handlers:" in line:
                ready_line = line
                break

        assert ready_line is not None, (
            "worker.py did not log a 'ready, handlers:' line within 30s "
            "(process may have exited early or hung)"
        )
        assert "export" in ready_line, f"'export' missing from ready line: {ready_line!r}"
        assert "transcribe" in ready_line, f"'transcribe' missing from ready line: {ready_line!r}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_worker_startup_survives_non_utf8_stdout(tmp_path):
    """Regression test for a production export crash:

        'charmap' codec can't encode character '→' in position 7:
        character maps to <undefined>

    helpers/render.py (and grade.py, pack_transcripts.py) print progress
    lines containing the Unicode arrow "→" (e.g. `print(f"concat →
    {out_path.name}")`). On Windows the worker process's stdout defaults to
    cp1252, which cannot encode that character, so the print crashes the
    job mid-export. helpers/stdio.configure_stdio() exists to fix exactly
    this by reconfiguring stdout/stderr to UTF-8 with errors="replace", but
    it was only ever called from render.py's own CLI main() -- never from
    the worker process that actually runs export jobs.

    This spawns the real worker module as a subprocess with the child's
    stdio forced to cp1252 (matching the production failure), then
    short-circuits startup right where jobs_mod.reconcile_stale(db) runs --
    before any job is claimed -- to print the same arrow character. No job
    is ever enqueued, claimed, or mutated; the fake reconcile stands in for
    the real one so no live database call is made.

    Pre-fix: the print raises UnicodeEncodeError and the subprocess exits
    non-zero. Post-fix: worker.py's startup call to configure_stdio() has
    already made stdout UTF-8-safe, so the print succeeds.
    """
    wrapper = tmp_path / "boot_and_print_arrow.py"
    wrapper.write_text(
        "import sys\n"
        f"sys.path.insert(0, {BACKEND_DIR!r})\n"
        "import worker\n"
        "\n"
        "\n"
        "def _fake_reconcile(db):\n"
        "    # Stands in for jobs_mod.reconcile_stale(db), which real worker.py\n"
        "    # startup calls before any job is claimed. No live DB is touched.\n"
        "    # Prints the same Unicode arrow helpers/render.py prints mid-export.\n"
        "    print(\"reconcile → ok\")\n"
        "    sys.stdout.flush()\n"
        "    raise SystemExit(0)\n"
        "\n"
        "\n"
        "worker.jobs_mod.reconcile_stale = _fake_reconcile\n"
        "worker.main()\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"
    env["DB_NAME"] = "clipcut_test"  # never touched for real; belt-and-braces.

    proc = subprocess.Popen(
        [sys.executable, str(wrapper)],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    try:
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            pytest.fail(
                f"worker subprocess did not exit within 30s.\n"
                f"stdout={out!r}\nstderr={err!r}"
            )

        assert "UnicodeEncodeError" not in err, (
            "worker subprocess crashed printing a Unicode arrow under cp1252 "
            f"stdout:\nstdout={out!r}\nstderr={err!r}"
        )
        assert "reconcile → ok" in out, (
            f"expected the arrow line in stdout.\nstdout={out!r}\nstderr={err!r}"
        )
        assert proc.returncode == 0, (
            f"worker subprocess exited {proc.returncode}.\n"
            f"stdout={out!r}\nstderr={err!r}"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
