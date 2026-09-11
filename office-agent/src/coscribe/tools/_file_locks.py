"""Per-file locking for tools that load-modify-save a shared document
across multiple calls (``write_xlsx``'s per-sheet appends,
``write_pptx``/``add_pptx_chart``/etc.'s per-slide edits).

Real, live-hit bug this fixes: LangGraph runs the tool_calls in one
AIMessage concurrently (each on its own worker thread, since these are
plain sync functions). Asked to build a 3-sheet workbook, a real model
proposed all 3 ``write_xlsx`` calls to the same ``sales.xlsx`` at once --
two of them raced on ``load_workbook()``/``Workbook.save()``, and the
loser read the file mid-write from the other, corrupting it
(``zipfile.BadZipFile: File is not a zip file``, since a partially-written
.xlsx/.pptx is just a broken zip). The model happened to notice the error
and retry sequentially on its own, but nothing forced that -- a less
careful model, or the same tool called concurrently for a reason other
than "build multiple sheets," would just corrupt the file silently.

A plain ``threading.Lock`` (not ``asyncio.Lock``) is correct here: these
tool functions are synchronous, and LangChain's default
``BaseTool.ainvoke()`` for a sync-only tool runs it via
``loop.run_in_executor(None, ...)`` -- the default thread pool executor --
so concurrent calls really are concurrent OS threads, not just concurrent
asyncio tasks sharing one thread. A blocking ``threading.Lock.acquire()``
inside one of those worker threads only blocks that worker, never the
event loop other tool calls/WS messages run on.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()

_F = TypeVar("_F", bound=Callable[..., Any])


def file_lock(path: Path) -> threading.Lock:
    """One lock per resolved absolute path, created lazily on first use
    and never removed -- a handful of long-lived Lock objects for the
    files a session actually touches is negligible, and correctness here
    depends on every caller for the same file always getting back the
    *same* Lock object, which a lock created fresh per call could never
    guarantee."""
    with _locks_guard:
        lock = _locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _locks[path] = lock
        return lock


def locked_by_path(method: _F) -> _F:
    """Serializes calls to a ``WorkspaceScope``-backed toolkit method
    whose first real argument is ``path`` (a str, relative to the
    workspace root) -- the whole call runs under that resolved file's
    lock, so a second call to the same file (from a concurrently-running
    tool call in the same turn) blocks until the first one's full
    load-modify-save-and-recalc/preview sequence finishes, instead of
    racing it. Applied as a decorator (not inlined `with file_lock(...)`
    in each method body) so it's one line to add, can't accidentally
    leave part of the critical section unlocked, and reads the same way
    at every call site.

    Uses ``self._scope.resolve(path, write=True)`` -- the exact same
    resolution every wrapped method's own ``_check_writable`` already
    does internally -- so the lock key matches what the method will
    actually operate on. Resolving it twice (once here, once inside the
    method) is harmless: `WorkspaceScope.resolve` has no side effects of
    its own beyond path math."""
    sig = inspect.signature(method)

    @wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        bound = sig.bind(self, *args, **kwargs)
        bound.apply_defaults()
        path = bound.arguments["path"]
        resolved = self._scope.resolve(path, write=True)
        with file_lock(resolved):
            return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]
