"""``run_background_script`` and friends -- the fire-and-forget counterpart
to ``tools/exec/scripts.py``'s ``run_python_script``/``tools/exec/node_scripts.py``'s
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
already gives for "come back when it's done" -- see tools/bookkeeping/selfwake.py's
`wake_on_task`, the direct extension of its existing `wake_on(job_id)` to
a second kind of "background thing that eventually finishes," this
module's `BackgroundTaskStore` as its new backing.

**Same isolation as the synchronous scripts** (see tools/exec/scripts.py's own
docstring): in the AppContainer where the machine passed the self-test, and
watched like a script let go on in the background, since a container process
is not an asyncio one; otherwise the approval prompt is the safety mechanism.

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
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...runtime import tool_metadata
from ..exec import (
    RunningCommand,
    blocked_write,
    ensure_node_env,
    ensure_script_env,
    isolation_for_scripts,
    node_guard_args,
    overlay_env,
    refusal_note,
    require_capability,
    script_scratch_root,
    start_command,
    suggested_folder,
    venv_python,
    without_guard_warnings,
    writable_roots,
    write_python_wrapper,
)

# An id comes from a URL in the panel, so it must never name a path outside
# the store.
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

VALID_LANGUAGES = ("python", "node")
# What a task ran: a script in one of the two languages, or a shell command line.
TASK_KINDS = (*VALID_LANGUAGES, "shell")
VALID_STATUSES = ("running", "succeeded", "failed", "timed_out", "killed", "interrupted")
FINISHED_STATUSES = tuple(status for status in VALID_STATUSES if status != "running")

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

# How long a killed process may take to be gone before its task is settled anyway.
_KILL_GRACE_SECONDS = 10.0

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
_LIVE: dict[str, tuple[BackgroundTask, asyncio.subprocess.Process | RunningCommand]] = {}


# One listener per conversation: the open tab's session, so the Background
# tasks panel refreshes when a script starts or ends instead of waiting for
# its poll. A replaced listener is a reopened session, never a second reader.
_LISTENERS: dict[str, Callable[[BackgroundTask], None]] = {}


def set_change_listener(thread_id: str, listener: Callable[[BackgroundTask], None]) -> None:
    _LISTENERS[thread_id] = listener


def _announce(task: BackgroundTask, loop: asyncio.AbstractEventLoop | None = None) -> None:
    """`loop` is the event loop the listener belongs to, when this runs on another thread."""
    listener = _LISTENERS.get(task.thread_id)
    if listener is None:
        return
    if loop is None:
        listener(task)
        return
    try:
        loop.call_soon_threadsafe(listener, task)
    except RuntimeError:  # the loop is closed: the app is shutting down
        pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class BackgroundTask:
    task_id: str
    thread_id: str
    language: str  # "python" | "node" | "shell"
    description: str
    status: str  # "running" | "succeeded" | "failed" | "timed_out" | "killed"
    started_at: str
    finished_at: str | None = None
    exit_code: int | None = None
    pid: int | None = None
    note: str | None = None

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
            "note": self.note,
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
            note=data.get("note"),
        )


class BackgroundTaskStore:
    """One JSON record + one plain-text log file per task, both under
    state_dir/background_tasks/ -- same "one file per request" shape as
    tools/bookkeeping/selfwake.py's WakeStore, plus a sibling .log file the JSON
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
        if not _SAFE_ID.fullmatch(task_id):
            return None
        path = self._json_path(task_id)
        if not path.exists():
            return None
        return self._settle_if_orphaned(
            BackgroundTask.from_dict(json.loads(path.read_text(encoding="utf-8")))
        )

    def list_for_thread(self, thread_id: str) -> list[BackgroundTask]:
        if not self.root.is_dir():
            return []
        tasks = [
            BackgroundTask.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(self.root.glob("*.json"))
        ]
        mine = sorted((t for t in tasks if t.thread_id == thread_id), key=lambda t: t.started_at)
        return [self._settle_if_orphaned(task) for task in mine]

    def _settle_if_orphaned(self, task: BackgroundTask) -> BackgroundTask:
        """A task this process holds no handle for lost its supervisor in a
        restart; left as "running" it would show as working forever, and a
        wake waiting on it would never fire. The process itself may still be
        running: only coscribe's record of it is gone."""
        if task.status != "running" or task.task_id in _LIVE:
            return task
        task.status = "interrupted"
        task.finished_at = task.finished_at or _now_iso()
        task.note = "coscribe restarted while it was running; the script may still be running."
        self.save(task)
        return task

    def delete(self, task_id: str) -> None:
        if not _SAFE_ID.fullmatch(task_id):
            return
        for path in (self._json_path(task_id), self.log_path(task_id)):
            path.unlink(missing_ok=True)

    def log_path(self, task_id: str) -> Path:
        return self.root / f"{task_id}.log"

    def _json_path(self, task_id: str) -> Path:
        return self.root / f"{task_id}.json"

    def tail(self, task_id: str, tail_bytes: int) -> str:
        if not _SAFE_ID.fullmatch(task_id):
            return ""
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


def _kill(task: BackgroundTask, proc: asyncio.subprocess.Process | RunningCommand) -> None:
    """A shell command's children outlive a kill of the shell alone, so a command
    is stopped as a whole tree; a script has no children worth chasing."""
    if task.language != "shell":
        proc.kill()
        return
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603 -- fixed arguments, a pid we started
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


async def _supervise(
    store: BackgroundTaskStore,
    task: BackgroundTask,
    proc: asyncio.subprocess.Process,
    scratch_dir: Path | None,
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
                _kill(task, proc)
                exit_code = await proc.wait()
    finally:
        _LIVE.pop(task.task_id, None)
        if scratch_dir is not None:
            shutil.rmtree(scratch_dir, ignore_errors=True)

    _settle(store, task, exit_code, timed_out)


def _settle(
    store: BackgroundTaskStore,
    task: BackgroundTask,
    exit_code: int,
    timed_out: bool,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
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
    _announce(task, loop)


def adopt_running_command(
    state_dir: str | Path,
    thread_id: str,
    command: RunningCommand,
    language: str,
    description: str,
    scratch_dir: Path | None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Takes over a script the person let go on in the background from a tool call that was
    waiting for it, or one started contained, and returns the new task's id. Runs on the tool's
    worker thread, so what must reach the event loop goes through `command.loop`."""
    store = BackgroundTaskStore(state_dir)
    task = BackgroundTask(
        task_id=uuid.uuid4().hex[:12],
        thread_id=thread_id,
        language=language,
        description=description,
        status="running",
        started_at=_now_iso(),
        pid=command.pid,
    )
    store.save(task)
    _LIVE[task.task_id] = (task, command)
    command.adopt(store.log_path(task.task_id))
    _announce(task, command.loop)
    threading.Thread(
        target=_watch,
        args=(store, task, command, scratch_dir, timeout),
        name="background-watch",
        daemon=True,
    ).start()
    return task.task_id


def _prepare_script(
    language: str,
    script: str,
    state: Path,
    scratch_root: Path,
    write_roots: list[str],
    base_env: dict[str, str],
) -> tuple[list[str], dict[str, str], Path, list[Path]]:
    """The argv, environment, scratch folder and runtime folders of a background script, with
    the script written into its scratch folder."""
    require_capability(state, "scripts")
    if language == "python":
        interpreter = str(venv_python(ensure_script_env(state)))
        scratch_dir = Path(tempfile.mkdtemp(prefix="coscribe_bg_script_", dir=scratch_root))
        script_path = scratch_dir / "script.py"
        # PYTHONIOENCODING/PYTHONUTF8 forced for the same reason
        # tools/exec/scripts.py's _run_python_script does -- without them, a
        # background script's own print() of non-Latin1 text (Chinese,
        # routine for this codebase's users) can crash inside the child
        # itself on a Windows machine with a non-UTF-8 locale, the
        # 'charmap' codec can't encode characters bug documented in
        # PPTX_DESIGN.md §23.
        env = {
            **base_env,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "MPLCONFIGDIR": str(scratch_dir),
        }
        wrapper, guard_config = write_python_wrapper(scratch_dir, [*write_roots, str(scratch_dir)])
        guard_args = [str(wrapper), str(guard_config)]
        runtime: list[Path] = []
    else:
        node_env_dir = ensure_node_env(state)
        interpreter = shutil.which("node") or "node"
        scratch_dir = Path(tempfile.mkdtemp(prefix="coscribe_bg_node_", dir=scratch_root))
        script_path = scratch_dir / "script.js"
        env = {**base_env, "NODE_PATH": str(node_env_dir / "node_modules")}
        guard_args = node_guard_args(interpreter, [*write_roots, str(scratch_dir)]) or []
        runtime = [node_env_dir, Path(interpreter).resolve().parent]
    # encoding="utf-8": Path.write_text has no UTF-8 default on Windows.
    script_path.write_text(script, encoding="utf-8")
    return [interpreter, *guard_args, str(script_path)], env, scratch_dir, runtime


async def _start_contained(
    state: Path,
    thread_id: str,
    root: Path,
    argv: Sequence[str] | str,
    env: dict[str, str],
    folders: dict[str, list[Path]],
    runtime: Sequence[Path],
    language: str,
    description: str,
    scratch_dir: Path | None,
    timeout: float,
) -> dict[str, Any] | None:
    """Starts the run in the container and hands it to `_watch`, or returns None when this
    machine cannot hold it (then the caller starts it as before)."""
    require_capability(state, "scripts", thread_id)
    isolation = await asyncio.to_thread(
        isolation_for_scripts, state, runtime=runtime, thread_id=thread_id, **folders
    )
    if isolation is None:
        return None
    if scratch_dir is not None:
        env = env | {"TEMP": str(scratch_dir), "TMP": str(scratch_dir)}
    loop = asyncio.get_running_loop()

    def start() -> str:
        # A container process is not an asyncio one: it is watched the way a script let go on
        # in the background is.
        command = RunningCommand(argv, str(root), env, isolation)
        command.loop = loop
        return adopt_running_command(
            state, thread_id, command, language, description, scratch_dir, timeout
        )

    task_id = await asyncio.to_thread(start)
    status = {"task_id": task_id, "status": "running"}
    return {**status, "language": language, "description": description}


def _watch(
    store: BackgroundTaskStore,
    task: BackgroundTask,
    command: RunningCommand,
    scratch_dir: Path | None,
    timeout: float,
) -> None:
    """`_supervise` for a process that is not an asyncio one. The time limit counts from the
    hand-over: the person chose to let it go on."""
    timed_out = False
    try:
        try:
            exit_code = command.wait_exit(timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill(task, command)
            try:
                exit_code = command.wait_exit(_KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                exit_code = -1
        # Settled before it leaves _LIVE: this runs off the event loop, and a status check in
        # between would take the task for an orphan and write the same record at once.
        _settle(store, task, exit_code, timed_out, command.loop)
    finally:
        _LIVE.pop(task.task_id, None)
        if scratch_dir is not None:
            shutil.rmtree(scratch_dir, ignore_errors=True)


def stop_background_task(
    state_dir: str | Path, task_id: str, thread_id: str | None = None
) -> dict[str, Any]:
    """Kill a running background script. Raises KeyError for an unknown task
    (or one of another conversation, when `thread_id` is given) and
    ValueError when it isn't running or this process holds no handle to it."""
    on_disk = BackgroundTaskStore(state_dir).load(task_id)
    if on_disk is None or (thread_id is not None and on_disk.thread_id != thread_id):
        raise KeyError(f"No background task with id {task_id!r} in this conversation")
    if on_disk.status != "running":
        raise ValueError(f"Task {task_id!r} is already {on_disk.status!r}, not running")
    live = _LIVE.get(task_id)
    if live is None:
        raise ValueError(
            f"No live process handle for task {task_id!r} in this coscribe-web process "
            "(it may have started before the server last restarted)"
        )
    # Mutate the same object _supervise holds, not `on_disk`: it reads this
    # object's status once the process exits to tell a kill from a failure.
    live_task, proc = live
    live_task.status = "killed"
    BackgroundTaskStore(state_dir).save(live_task)
    _kill(live_task, proc)
    return live_task.to_dict()


def forget_finished_background_tasks(state_dir: str | Path, thread_id: str) -> int:
    store = BackgroundTaskStore(state_dir)
    removed = 0
    for task in store.list_for_thread(thread_id):
        if task.status in FINISHED_STATUSES:
            store.delete(task.task_id)
            removed += 1
    return removed


def read_background_log(
    state_dir: str | Path, task_id: str, tail_bytes: int = _DEFAULT_TAIL_BYTES
) -> dict[str, Any] | None:
    """The task's record and the end of its log, for the panel. None when
    there is no such task. Output is filtered the way check_background_task's
    is, so the script guard's own notices never read as the script's."""
    store = BackgroundTaskStore(state_dir)
    task = store.load(task_id)
    if task is None:
        return None
    tail_bytes = min(max(tail_bytes, 1), _MAX_TAIL_BYTES)
    _blocked, output = blocked_write(without_guard_warnings(store.tail(task_id, tail_bytes)))
    return {"task": task.to_dict(), "output": output}


def build_background_task_tools(
    thread_id: str,
    workspace_root: str | Path,
    state_dir: str | Path,
    *,
    extra_writable: Sequence[str | Path] = (),
    extra_readable: Sequence[str | Path] = (),
    session_env: Callable[[], Mapping[str, str]] | None = None,
) -> list[Callable[..., Any]]:
    """Return the tool callables the Coordinator agent can call. `state_dir`
    is required for the same reason run_python_script's is -- the script-env/
    node-env directories a background script depends on have nowhere else
    to live, and so do this module's own task records/log files."""
    root = Path(workspace_root)
    state = Path(state_dir)
    store = BackgroundTaskStore(state)
    write_roots = writable_roots(root, [Path(p) for p in extra_writable])
    folders = {
        "writable": [root, *(Path(p) for p in extra_writable)],
        "readable": [Path(p) for p in extra_readable],
    }
    scratch_root = script_scratch_root(state)

    async def run_background_script(
        language: str, script: str, description: str, timeout_seconds: float = _DEFAULT_TIMEOUT
    ) -> dict[str, Any]:
        """Start a Python or Node.js script running in the background and return
        immediately -- for a script expected to take longer than
        run_python_script/run_node_script's own 600-second hard cap (a big
        OCR/data-processing job, a long batch pipeline), where waiting
        synchronously would tie up the whole conversation. It is held to the
        same folders as those two tools: on Windows, when this computer passed
        coscribe's isolation check, the script runs isolated: it can read the
        workspace and the folders added to this conversation, and write in the
        workspace and the read-write ones, but cannot delete, rename or move
        files there, nor reach any other folder; use delete_file or move_file
        for those. If the check failed, the run is refused unless the user allowed
        it; unisolated, only writes outside those folders are refused. Runs with
        its working directory set to the workspace root and against the exact
        same dedicated Python/Node environment
        run_python_script/run_node_script use (same pre-installed packages, same
        "you manage extra packages from the Capabilities settings tab" rule).

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

        base_env = overlay_env(os.environ, session_env() if session_env else None)
        argv, env, scratch_dir, runtime = await asyncio.to_thread(
            _prepare_script, language, script, state, scratch_root, write_roots, base_env
        )
        started = await _start_contained(
            state, thread_id, root, argv, env, folders, runtime, language, description,
            scratch_dir, timeout,
        )
        if started is not None:
            return started
        proc = await asyncio.create_subprocess_exec(
            *argv,
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
        return _register(proc, language, description, scratch_dir, timeout)

    async def run_background_command(
        command: str, description: str, timeout_seconds: float = _DEFAULT_TIMEOUT
    ) -> dict[str, Any]:
        """Start a shell command line running in the background and return
        immediately -- for a long command (a build, a download, a sync) that
        would tie up the conversation, or should run while you do other work.
        It runs through the machine's shell (sh, or cmd on Windows) in the
        workspace root, once the person turned commands on in Settings ›
        Capabilities. Where the command sandbox is set up (Windows) it can
        write only this conversation's folders, can't read the person's
        files elsewhere, and reaches the network only if the environment
        allows it; otherwise it runs as the user, can change any file, and
        the person approves each one. Prefer run_background_script when you
        can.

        This call does NOT return the command's output -- only a task_id
        confirming it started. Use check_background_task(task_id) to read its
        output/status later, wake_on_task(task_id, reason) to end this turn
        and be resumed once it finishes, or kill_background_task(task_id) to
        stop it (the command and the processes it started).

        Args:
            command: the command line, exactly as typed in a terminal.
            description: one sentence, plain language, what this command
                does -- shown with the command wherever it is presented for
                approval.
            timeout_seconds: how long it may run before being killed (capped
                at 21600 = 6 hours).
        """
        if not command.strip():
            raise ValueError("command must not be empty")
        timeout = min(max(timeout_seconds, 1.0), _MAX_TIMEOUT)
        env = overlay_env(os.environ, session_env() if session_env else None)
        proc = await start_command(state, thread_id, root, folders, command, env)
        return _register(proc, "shell", description, None, timeout)

    def _register(
        proc: asyncio.subprocess.Process,
        language: str,
        description: str,
        scratch_dir: Path | None,
        timeout: float,
    ) -> dict[str, Any]:
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
        _announce(task)

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
        blocked, output = blocked_write(without_guard_warnings(store.tail(task_id, tail_bytes)))
        result["output"] = output + (refusal_note(blocked) if blocked else "")
        if blocked:
            result["blocked_write"] = blocked
            folder = suggested_folder(blocked)
            if folder:
                result["blocked_folder"] = folder
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
        return stop_background_task(state, task_id, thread_id)

    return [
        tool_metadata(run_background_script, risk_category="EXEC", category="background_tasks"),
        tool_metadata(run_background_command, risk_category="EXEC", category="background_tasks"),
        tool_metadata(check_background_task, risk_category="READ", category="background_tasks"),
        tool_metadata(list_background_tasks, risk_category="READ", category="background_tasks"),
        tool_metadata(
            kill_background_task, risk_category="WRITE_LOCAL", category="background_tasks"
        ),
    ]
