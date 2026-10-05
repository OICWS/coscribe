"""A client for `codex app-server`: newline-delimited JSON-RPC over the
child's stdio, requests both ways.

Notifications and server->client requests that name a thread go to that
thread's listener; a request nobody can answer gets a JSON-RPC error rather
than silence, since Codex would otherwise wait on it forever. A listener
gets its notifications in order from one queue, so a reply's pieces stay
in sequence and turn/completed comes after everything sent before it,
while the reader goes on reading. Each server request is answered from its
own task: an approval waits on a person.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# A single message can carry a whole file's patch or a long command output.
_LINE_LIMIT = 64 * 1024 * 1024
_CLOSE_GRACE_SECONDS = 2.0
# A Codex that doesn't answer initialize would hold every later turn.
_START_SECONDS = 60.0
# One log per state_dir, appended across restarts.
_LOG_LIMIT = 5 * 1024 * 1024


class AppServerError(RuntimeError):
    pass


class AppServerClosed(AppServerError):
    pass


class RequestFailed(AppServerError):
    def __init__(self, method: str, error: dict[str, Any]) -> None:
        super().__init__(f"{method} failed: {error.get('message') or error}")
        self.error = error


class ThreadListener(Protocol):
    async def notification(self, method: str, params: dict[str, Any]) -> None: ...

    async def server_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...

    def closed(self, error: AppServerClosed) -> None: ...


class AppServer:
    def __init__(self, argv: Sequence[str], env: dict[str, str], log_path: Path) -> None:
        self._argv = list(argv)
        self._env = env
        self._log_path = log_path
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._next_id = 0
        self._pending: dict[int, tuple[str, asyncio.Future[dict[str, Any]]]] = {}
        self._listeners: dict[str, _Subscription] = {}
        self._request_tasks: set[asyncio.Task[None]] = set()
        self._closed: AppServerClosed | None = None
        self._write_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._process is not None and self._closed is None

    async def start(self, client_name: str, client_version: str) -> dict[str, Any]:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            if self._log_path.stat().st_size > _LOG_LIMIT:
                os.replace(self._log_path, self._log_path.with_suffix(".log.1"))
        log = open(self._log_path, "ab")  # noqa: SIM115 -- the child keeps writing to it
        # Its own process group, so close() also ends the shells and scripts
        # Codex started, not only Codex.
        group: dict[str, Any]
        if sys.platform == "win32":
            group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        else:
            group = {"start_new_session": True}
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
                env=self._env,
                limit=_LINE_LIMIT,
                **group,
            )
        finally:
            log.close()
        self._reader = asyncio.create_task(self._read_loop())
        try:
            result = await asyncio.wait_for(
                self.request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": client_name,
                            "title": "coscribe",
                            "version": client_version,
                        },
                        # dynamicTools on thread/start needs it.
                        "capabilities": {"experimentalApi": True},
                    },
                ),
                _START_SECONDS,
            )
        except TimeoutError as exc:
            raise AppServerError(
                f"Codex didn't start: no answer in {_START_SECONDS:.0f} seconds."
            ) from exc
        await self._send({"method": "initialized", "params": {}})
        return result

    def listen(self, thread_id: str, listener: ThreadListener) -> None:
        self.unlisten(thread_id)
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()
        task = asyncio.create_task(_deliver(listener, queue))
        self._listeners[thread_id] = _Subscription(listener, queue, task)

    def unlisten(self, thread_id: str) -> None:
        subscription = self._listeners.pop(thread_id, None)
        if subscription is not None:
            subscription.queue.put_nowait(None)

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._closed is not None:
            raise self._closed
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (method, future)
        try:
            await self._send({"id": request_id, "method": method, "params": params})
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or self._closed is not None:
            raise self._closed or AppServerClosed("Codex isn't running.")
        data = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
        async with self._write_lock:
            try:
                process.stdin.write(data)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise AppServerClosed("Codex stopped unexpectedly.") from exc

    async def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        stdout = self._process.stdout
        try:
            while line := await stdout.readline():
                try:
                    message = json.loads(line)
                except ValueError:
                    logger.warning("codex app-server sent a line that isn't JSON: %r", line[:200])
                    continue
                self._dispatch(message)
        except (asyncio.LimitOverrunError, ValueError):
            # The stream can't be resynchronised after a cut line.
            logger.warning("codex app-server sent an oversized message", exc_info=True)
            _kill_group(self._process.pid)
        returncode = await self._process.wait()
        self._fail_all(AppServerClosed(f"Codex stopped (exit code {returncode})."))

    def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if "id" in message and method is None:
            entry = self._pending.get(message["id"])
            if entry is None:
                return
            name, future = entry
            if future.done():
                return
            if "error" in message:
                future.set_exception(RequestFailed(name, message["error"] or {}))
            else:
                future.set_result(message.get("result") or {})
            return
        params = message.get("params") or {}
        subscription = self._listeners.get(str(params.get("threadId", "")))
        if "id" in message:
            listener = subscription.listener if subscription is not None else None
            task = asyncio.create_task(self._answer(message["id"], str(method), params, listener))
            self._request_tasks.add(task)
            task.add_done_callback(self._request_tasks.discard)
            return
        if subscription is not None:
            subscription.queue.put_nowait((str(method), params))

    async def _answer(
        self,
        request_id: Any,
        method: str,
        params: dict[str, Any],
        listener: ThreadListener | None,
    ) -> None:
        reply: dict[str, Any]
        if listener is None:
            reply = {
                "id": request_id,
                "error": {"code": -32601, "message": f"{method}: no handler"},
            }
        else:
            try:
                reply = {"id": request_id, "result": await listener.server_request(method, params)}
            except Exception as exc:  # noqa: BLE001 -- Codex must get an answer either way
                reply = {"id": request_id, "error": {"code": -32603, "message": str(exc)}}
        with contextlib.suppress(AppServerClosed):
            await self._send(reply)

    def _fail_all(self, error: AppServerClosed) -> None:
        if self._closed is None:
            self._closed = error
        for _name, future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        for subscription in list(self._listeners.values()):
            try:
                subscription.listener.closed(error)
            except Exception:  # noqa: BLE001
                logger.warning("codex listener failed on close", exc_info=True)

    async def close(self) -> None:
        process = self._process
        if process is None:
            return
        self._fail_all(AppServerClosed("Codex was shut down."))
        if sys.platform == "win32":
            # taskkill /T finds children through a live parent only; once
            # Codex exits, the shells it started can't be reached.
            _kill_group(process.pid)
        if process.returncode is None:
            if process.stdin is not None:
                with contextlib.suppress(Exception):
                    process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), _CLOSE_GRACE_SECONDS)
            except TimeoutError:
                pass
        _kill_group(process.pid)
        with contextlib.suppress(ProcessLookupError):
            await process.wait()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        for task in list(self._request_tasks):
            task.cancel()
        for subscription in self._listeners.values():
            subscription.task.cancel()


class _Subscription:
    def __init__(
        self,
        listener: ThreadListener,
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None],
        task: asyncio.Task[None],
    ) -> None:
        self.listener = listener
        self.queue = queue
        self.task = task


async def _deliver(
    listener: ThreadListener, queue: asyncio.Queue[tuple[str, dict[str, Any]] | None]
) -> None:
    while (item := await queue.get()) is not None:
        method, params = item
        try:
            await listener.notification(method, params)
        except Exception:  # noqa: BLE001 -- one bad event mustn't stop the stream
            logger.warning("codex notification %s failed in its listener", method, exc_info=True)


def _kill_group(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)
