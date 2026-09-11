"""``run_background_script`` and friends -- the fire-and-forget counterpart
to ``tools/scripts.py``'s ``run_python_script``/``tools/node_scripts.py``'s
``run_node_script``, for a script expected to run *longer* than those two
tools' own 600s hard cap (a long OCR/data-processing job, a batch pipeline)
without blocking the conversation for the whole run.

Modeled on `pi-background-tasks` (github.com/earendil-works/pi), the one
harness among the ones surveyed (Codex CLI, Claude Code, DeepSeek Harness,
openworker) that actually ships this as a *separate* tool from its normal
exec tool, rather than a flag on it -- deliberately followed here for the
same reason: "started" and "the complete execution result" are different
enough shapes (one is a handle, one is stdout/stderr/exit_code) that
cramming both into run_python_script's own return type would make its
result ambiguous depending on a boolean the model has to remember to check.

**Fire-and-forget, not fire-and-stream.** Pi's own design deliberately
doesn't push live output either -- a durable log file plus a *bounded*,
on-demand read (`check_background_task`'s `tail_bytes`), not a live
WebSocket stream. Simpler, and matches what selfwake.py's wake mechanism
already gives for "come back when it's done" -- see tools/selfwake.py's
`wake_on_task`, the direct extension of its existing `wake_on(job_id)` to
a second kind of "background thing that eventually finishes," this
module's `BackgroundTaskStore` as its new backing.

**Same no-sandbox posture as the synchronous scripts** (see tools/scripts.py's
own docstring for the full reasoning) -- the approval prompt before a
background script starts is the only safety mechanism, same as the
synchronous tools' approval prompt before they run.

**Known v1 limitation, not fixed (documented, not silently ignored, same
posture as Phase 4's "poller-triggered resume doesn't stream into an open
tab" limitation)**: a running background task's supervising asyncio.Task
and live Process handle only exist in this coscribe-web process's own
memory (`_LIVE`/`_RUNNING_SUPERVISORS` below). If the server process
restarts while a task is mid-run, the durable on-disk record is orphaned
at status="running" forever -- nothing left to ever mark it finished, so
a pending wake_on_task for it never resolves either. Re-adopting orphaned
processes by PID on startup would fix this but is real extra machinery,
not justified for a first version of a feature whose whole premise is
"the desktop app stays running for the length of one script."
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..runtime.types import tool_metadata
from .node_env import ensure_node_env
from .script_env import ensure_script_env, venv_python

VALID_LANGUAGES = ("python", "node")
VALID_STATUSES = ("running", "succeeded", "failed", "timed_out", "killed")

# Deliberately much larger than run_python_script/run_node_script's own
# 120s default / 600s cap -- the whole reason to reach for the background
# version instead of the synchronous one is a script expected to run past
# that cap. 30 minutes covers the OCR/batch-processing scale this was
# built for; 6 hours is a generous outer bound for a genuinely long job
# without leaving an approved-but-runaway script able to hang around
# indefinitely.
_DEFAULT_TIMEOUT = 1800.0
_MAX_TIMEOUT = 21600.0

# Bytes, not lines -- matches Pi's own `/logs <id> <bytes>` bounded read.
# Large enough for a normal script's output to fit whole; small enough
# that a runaway script's megabytes of stdout can't blow up a single tool
# result.
_DEFAULT_TAIL_BYTES = 4000
_MAX_TAIL_BYTES = 200_000

# Read chunk size for the supervisor's incremental log-writing loop.
_READ_CHUNK_BYTES = 65536

# Holds a strong reference to every in-flight supervisor task so it can't
# be garbage-collected mid-run (asyncio only weakly tracks a Task once
# nothing else references it -- see asyncio.create_task's own warning).
# Process-wide, not per-thread/per-session, since the supervisor coroutine
# itself already carries its own thread_id/task_id closure state; this set
# exists purely to keep it alive, never to look anything up.
_RUNNING_SUPERVISORS: set[asyncio.Task[None]] = set()

# task_id -> (the live BackgroundTask object _supervise itself owns, its
# Process handle). kill_background_task mutates the *same* BackgroundTask
# object here (not a freshly disk-reloaded copy) specifically so
# _supervise's own finally block -- which also has this exact object, not
# a copy -- can tell "killed on purpose" apart from "just exited/timed
# out" once the process actually dies; a copy would lose that distinction
# to a real race (kill_background_task's own status="killed" write getting
# silently clobbered by _supervise's post-exit "failed", since a killed
# process's exit code looks like any other failure). Populated when a task
# starts, popped by the supervisor's own finally block -- see this
# module's docstring for why this doesn't survive a server restart.
_LIVE: dict[str, tuple[BackgroundTask, asyncio.subprocess.Process]] = {}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class BackgroundTask:
    task_id: str
    thread_id: str
    language: str  # "python" | "node"
    description: str
    status: str  # "running" | "succeeded" | "failed" | "timed_out" | "killed"
    started_at: str
    finished_at: str | None = None
    exit_code: int | None = None
    pid: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "thread_id": self.thread_id,
            "language": self.language,
            "description": self.description,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "pid": self.pid,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackgroundTask:
        return cls(
            task_id=data["task_id"],
            thread_id=data["thread_id"],
            language=data["language"],
            description=data["description"],
            status=data.get("status", "running"),
            started_at=data["started_at"],
            finished_at=data.get("finished_at"),
            exit_code=data.get("exit_code"),
            pid=data.get("pid"),
        )


class BackgroundTaskStore:
    """One JSON record + one plain-text log file per task, both under
    state_dir/background_tasks/ -- same "one file per request" shape as
    tools/selfwake.py's WakeStore, plus a sibling .log file the JSON
    record doesn't try to also hold (unbounded script output has no
    business living inside a small JSON status file re-read/re-written on
    every status change)."""

    def __init__(self, state_dir: str | Path) -> None:
        self.root = Path(state_dir) / "background_tasks"

    def save(self, task: BackgroundTask) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._json_path(task.task_id)
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(task.to_dict(), handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    def load(self, task_id: str) -> BackgroundTask | None:
        path = self._json_path(task_id)
        if not path.exists():
            return None
        return BackgroundTask.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_for_thread(self, thread_id: str) -> list[BackgroundTask]:
        if not self.root.is_dir():
            return []
        tasks = [
            BackgroundTask.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(self.root.glob("*.json"))
        ]
        return [t for t in tasks if t.thread_id == thread_id]

    def log_path(self, task_id: str) -> Path:
        return self.root / f"{task_id}.log"

    def _json_path(self, task_id: str) -> Path:
        return self.root / f"{task_id}.json"

    def tail(self, task_id: str, tail_bytes: int) -> str:
        path = self.log_path(task_id)
        if not path.is_file():
            return ""
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > tail_bytes:
                handle.seek(size - tail_bytes)
            data = handle.read()
        text = data.decode("utf-8", errors="replace")
        # Only real when the file was actually truncated from the front,
        # not merely because tail_bytes happens to equal the file size.
        if size > tail_bytes:
            return f"[... {size - tail_bytes} earlier bytes omitted ...]\n{text}"
        return text


async def _supervise(
    store: BackgroundTaskStore,
    task: BackgroundTask,
    proc: asyncio.subprocess.Process,
    scratch_dir: Path,
    timeout_seconds: float,
) -> None:
    """Owns a started process end to end: streams its combined stdout/
    stderr into the durable log file as it arrives (so check_background_task
    can read partial output while still running, not just after), then
    finalizes the JSON record's terminal status. Runs as its own
    asyncio.Task, independent of whatever tool call originally started
    it -- by the time this coroutine finishes, the turn that called
    run_background_script is long over."""
    log_path = store.log_path(task.task_id)
    timed_out = False
    try:
        with log_path.open("wb") as log_handle:
            assert proc.stdout is not None  # PIPE was requested below

            async def _drain_then_wait() -> int:
                while True:
                    chunk = await proc.stdout.read(_READ_CHUNK_BYTES)  # type: ignore[union-attr]
                    if not chunk:
                        break
                    log_handle.write(chunk)
                    log_handle.flush()
                return await proc.wait()

            try:
                # One timeout budget for the whole run (drain + exit), not
                # one each -- two separate wait_for calls back to back
                # would let a script take up to 2x timeout_seconds before
                # this actually gives up on it.
                exit_code = await asyncio.wait_for(_drain_then_wait(), timeout=timeout_seconds)
            except TimeoutError:
                timed_out = True
                proc.kill()
                exit_code = await proc.wait()
    finally:
        _LIVE.pop(task.task_id, None)
        shutil.rmtree(scratch_dir, ignore_errors=True)

    task.finished_at = _now_iso()
    task.exit_code = exit_code
    if timed_out:
        task.status = "timed_out"
    elif task.status == "killed":
        # kill_background_task already mutated this *same* BackgroundTask
        # object (see _LIVE's own docstring) and saved it -- don't
        # overwrite a deliberate kill with "failed" just because the
        # process's own exit code (from being killed) looks like any
        # other failure.
        pass
    else:
        task.status = "succeeded" if exit_code == 0 else "failed"
    store.save(task)


def build_background_task_tools(
    thread_id: str, workspace_root: str | Path, state_dir: str | Path
) -> list[Callable[..., Any]]:
    """Return the tool callables the Coordinator agent can call. `state_dir`
    is required for the same reason run_python_script's is -- the script-env/
    node-env directories a background script depends on have nowhere else
    to live, and so do this module's own task records/log files."""
    root = Path(workspace_root)
    state = Path(state_dir)
    store = BackgroundTaskStore(state)

    async def run_background_script(
        language: str, script: str, description: str, timeout_seconds: float = _DEFAULT_TIMEOUT
    ) -> dict[str, Any]:
        """Start a Python or Node.js script running in the background and
        return immediately -- for a script expected to take longer than
        run_python_script/run_node_script's own 600-second hard cap (a
        big OCR/data-processing job, a long batch pipeline), where waiting
        synchronously would tie up the whole conversation. Like those two
        tools, there is no sandbox around it (see this module's docstring):
        the script can read/write any file the coscribe process can reach
        and make any network call. Runs with its working directory set to
        the workspace root and against the exact same dedicated Python/
        Node environment run_python_script/run_node_script use (same
        pre-installed packages, same "you manage extra packages from the
        Environment settings tab" rule).

        This call does NOT return the script's output -- only a task_id
        confirming it started. Use check_background_task(task_id) to read
        its output/status later (works both while it's still running and
        after), or wake_on_task(task_id, reason) to end this turn and get
        automatically resumed once it finishes, instead of polling.

        Args:
            language: "python" or "node".
            script: the complete source to run.
            description: one sentence, plain language, what this script
                does -- shown alongside the script text wherever this call
                is presented for approval, same as run_python_script's own.
            timeout_seconds: how long the script may run before being
                killed (capped at 21600 = 6 hours). Far more generous than
                run_python_script's 600s cap on purpose -- the whole point
                of reaching for this tool instead is a script expected to
                run long.
        """
        if language not in VALID_LANGUAGES:
            raise ValueError(f"language must be one of {VALID_LANGUAGES}, got {language!r}")
        timeout = min(max(timeout_seconds, 1.0), _MAX_TIMEOUT)

        if language == "python":
            venv_dir = await asyncio.to_thread(ensure_script_env, state)
            interpreter = str(venv_python(venv_dir))
            scratch_dir = Path(tempfile.mkdtemp(prefix="coscribe_bg_script_"))
            script_path = scratch_dir / "script.py"
            env = None
        else:
            node_env_dir = await asyncio.to_thread(ensure_node_env, state)
            interpreter = shutil.which("node") or "node"
            scratch_dir = Path(tempfile.mkdtemp(prefix="coscribe_bg_node_script_"))
            script_path = scratch_dir / "script.js"
            env = {**os.environ, "NODE_PATH": str(node_env_dir / "node_modules")}
        script_path.write_text(script)

        proc = await asyncio.create_subprocess_exec(
            interpreter,
            str(script_path),
            cwd=str(root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            # Merged into stdout -- one combined, chronological log, same
            # as a user reading a terminal would see (and matching how
            # this feature's own inspiration -- Claude Code's own
            # background-task panel -- shows a single "2>&1"-merged
            # stream, not two separate ones to correlate by hand).
            stderr=asyncio.subprocess.STDOUT,
        )

        task = BackgroundTask(
            task_id=uuid.uuid4().hex[:12],
            thread_id=thread_id,
            language=language,
            description=description,
            status="running",
            started_at=_now_iso(),
            pid=proc.pid,
        )
        store.save(task)
        _LIVE[task.task_id] = (task, proc)

        supervisor = asyncio.create_task(_supervise(store, task, proc, scratch_dir, timeout))
        _RUNNING_SUPERVISORS.add(supervisor)
        supervisor.add_done_callback(_RUNNING_SUPERVISORS.discard)

        return {
            "task_id": task.task_id,
            "status": "running",
            "language": language,
            "description": description,
        }

    def check_background_task(
        task_id: str, tail_bytes: int = _DEFAULT_TAIL_BYTES
    ) -> dict[str, Any]:
        """Check a background script's status and read its output -- works
        whether it's still running (partial output so far) or already
        finished (full output, plus exit_code). Call this to poll instead
        of using wake_on_task when you'd rather keep working on something
        else in the meantime and check back yourself.

        Args:
            task_id: id returned by run_background_script.
            tail_bytes: how many bytes of output to return, counted from
                the end (capped at 200000) -- a long-running script's
                output can be large; this bounds one call's result to the
                most recent, usually most relevant, part of it.
        """
        task = store.load(task_id)
        if task is None or task.thread_id != thread_id:
            raise KeyError(f"No background task with id {task_id!r} in this conversation")
        tail_bytes = min(max(tail_bytes, 1), _MAX_TAIL_BYTES)
        result = task.to_dict()
        result["output"] = store.tail(task_id, tail_bytes)
        return result

    def list_background_tasks() -> list[dict[str, Any]]:
        """List this conversation's own background tasks (running or
        finished) -- use this to check what's currently in flight without
        needing a specific task_id."""
        return [t.to_dict() for t in store.list_for_thread(thread_id)]

    async def kill_background_task(task_id: str) -> dict[str, Any]:
        """Kill a running background script. No-op-with-a-clear-error if
        it already finished, or if coscribe-web was restarted since it
        started (this process no longer holds a live handle to it -- see
        this module's docstring).

        Args:
            task_id: id returned by run_background_script.
        """
        on_disk = store.load(task_id)
        if on_disk is None or on_disk.thread_id != thread_id:
            raise KeyError(f"No background task with id {task_id!r} in this conversation")
        if on_disk.status != "running":
            raise ValueError(f"Task {task_id!r} is already {on_disk.status!r}, not running")
        live = _LIVE.get(task_id)
        if live is None:
            raise RuntimeError(
                f"No live process handle for task {task_id!r} in this coscribe-web process "
                "(it may have started before the server last restarted)"
            )
        # Mutate the *same* BackgroundTask object _supervise itself holds
        # (not on_disk, a separate copy freshly deserialized above) -- see
        # _LIVE's own docstring for why this matters: _supervise checks
        # this exact object's .status once the process actually exits, to
        # tell a deliberate kill apart from a plain failure.
        live_task, proc = live
        live_task.status = "killed"
        store.save(live_task)
        proc.kill()
        return live_task.to_dict()

    return [
        tool_metadata(run_background_script, risk_category="EXEC", category="background_tasks"),
        tool_metadata(check_background_task, risk_category="READ", category="background_tasks"),
        tool_metadata(list_background_tasks, risk_category="READ", category="background_tasks"),
        tool_metadata(
            kill_background_task, risk_category="WRITE_LOCAL", category="background_tasks"
        ),
    ]
