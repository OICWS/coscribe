"""One Codex thread driven a turn at a time.

Every command and file change Codex wants comes back as an approval and is
settled by the caller's `decide`; the thread runs with no sandbox
(`danger-full-access`, by decision -- see ROADMAP), so that answer is the
only gate. The one exception: a plain read of the thread's folder runs
without asking -- the user opened that folder for this work, so reading it
needs no further permission. "Plain read" is decided here, from a short
list of read-only programs and no shell syntax at all, not from Codex's own
`commandActions`: that parse is for display, and live it called
`find . -delete` and `cat x | tee y` reads.

Files written are found by comparing the folder before and after a turn:
Codex mostly writes through the shell, which reports no file-change item.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shlex
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, Literal

from .client import AppServer, AppServerClosed, RequestFailed
from .launch import CodexHost, CodexModel

logger = logging.getLogger(__name__)

_INTERRUPT_SECONDS = 10.0
# As runtime_lg/subagents.py's MODEL_STALL_SECONDS: a turn that hears
# nothing this long, with no command running and no approval pending, is
# taken as hung -- otherwise a lost turn/completed would wait forever.
STALL_SECONDS = 180.0
_SNAPSHOT_LIMIT = 20000
_SKIPPED_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv"})
_READ_PROGRAMS = frozenset(
    {"ls", "cat", "head", "tail", "wc", "grep", "rg", "find", "pwd", "stat", "du"}
)
# Chaining, pipes, redirects, expansion and globs all let one "read" do
# more, or reach past the folder (`~`, `$HOME`, `.*`); a command using any
# of them is asked about rather than parsed.
_SHELL_SYNTAX = frozenset("|&;<>$`(){}[]*?~!\\\n\r")
_FIND_ACTIONS = ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fls")
_SHELLS = frozenset({"bash", "sh", "zsh"})
# On Windows Codex runs PowerShell. Its read cmdlets and their aliases, none
# of which takes a parameter that writes; anything a script might chain onto
# one (`; Remove-Item`, a pipe, a sub-expression) is shell syntax and is
# turned away before the words are read.
_POWERSHELLS = frozenset({"powershell", "powershell.exe", "pwsh", "pwsh.exe"})
_POWERSHELL_READS = frozenset(
    {
        "get-childitem", "gci", "dir", "ls",
        "get-content", "gc", "cat", "type",
        "select-string", "sls",
        "get-item", "gi",
        "get-location", "gl", "pwd",
        "test-path",
    }
)  # fmt: skip
# `*` and `?` stay: a wildcard in a name is how PowerShell lists a folder's
# csv files, and the folder check below doesn't depend on what it matches.
_POWERSHELL_SYNTAX = frozenset("|&;<>$`(){}[]~!\n\r,@#")
_WINDOWS_DEVICES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)
# Codex puts this on every PowerShell command so output comes back as UTF-8.
# It is brackets and a semicolon, which the read check would turn away, so it
# comes off before the script is read (and before it is shown on a card).
_POWERSHELL_UTF8 = re.compile(
    r"\[Console\]::OutputEncoding\s*=\s*\[System\.Text\.Encoding\]::UTF8\s*(?:;|\r?\n)\s*",
    re.IGNORECASE,
)
_POWERSHELL_FLAGS = frozenset({"-noprofile", "-nologo", "-noninteractive", "-nop", "-nol", "-noni"})
_POWERSHELL_FLAGS_WITH_VALUE = frozenset({"-executionpolicy", "-ep"})
# One `cd <dir> &&` in front; the directory is a plain word or a quoted
# string with nothing in it the shell would expand.
_CD_PREFIX = re.compile(
    r"cd\s+(?P<dir>\"[^\"$`\\]*\"|'[^']*'|[^\s\"'$`\\;&|<>()]+)\s*&&\s*(?P<rest>.+)",
    re.DOTALL,
)
_STATUSES: dict[str, Literal["completed", "interrupted", "failed"]] = {
    "completed": "completed",
    "interrupted": "interrupted",
}


@dataclass(frozen=True)
class ApprovalRequest:
    kind: Literal["command", "file_change"]
    item_id: str
    command: str = ""
    cwd: str = ""
    reason: str = ""
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class CodexEvent:
    """What a watcher sees of a turn: "text" (a piece of a reply),
    "message" (a reply, whole), "command_started"/"command_finished",
    "file_change", "usage", and
    "auto_approved" for a read-only command that ran without asking."""

    kind: str
    data: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    # The last request's input: how full the context is now.
    context_tokens: int = 0

    def add(self, last: dict[str, Any]) -> None:
        self.input_tokens += int(last.get("inputTokens") or 0)
        self.cached_input_tokens += int(last.get("cachedInputTokens") or 0)
        self.output_tokens += int(last.get("outputTokens") or 0)
        self.context_tokens = int(last.get("inputTokens") or 0)


@dataclass
class TurnResult:
    status: Literal["completed", "interrupted", "failed"]
    text: str
    error: str | None = None
    usage: Usage = field(default_factory=Usage)
    files_changed: list[str] = field(default_factory=list)
    commands: int = 0


Decide = Callable[[ApprovalRequest], Awaitable[bool]]
OnEvent = Callable[[CodexEvent], Awaitable[None]]


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    seen: dict[str, tuple[int, int]] = {}
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIPPED_DIRS]
        for name in files:
            path = Path(directory) / name
            with contextlib.suppress(OSError):
                stat = path.stat()
                seen[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
            if len(seen) >= _SNAPSHOT_LIMIT:
                return seen
    return seen


def changed_files(
    before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]
) -> list[str]:
    return sorted(path for path, state in after.items() if before.get(path) != state)


def _word_spans(line: str) -> list[tuple[int, str]] | None:
    """The words of a command line, each with where it starts. Quotes group,
    backslashes are ordinary: on Windows they are the path separator, which
    shlex would eat."""
    words: list[tuple[int, str]] = []
    current: list[str] = []
    start = 0
    quote = ""
    started = False
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
            else:
                current.append(char)
        elif char in "\"'":
            if not started and not current:
                start = index
            quote = char
            started = True
        elif char.isspace():
            if started or current:
                words.append((start, "".join(current)))
                current, started = [], False
        else:
            if not started and not current:
                start = index
            current.append(char)
    if quote:
        return None
    if started or current:
        words.append((start, "".join(current)))
    return words


def _words(line: str) -> list[str] | None:
    spans = _word_spans(line)
    return None if spans is None else [word for _, word in spans]


def _unquoted(text: str) -> str:
    """`text` without the one pair of quotes that wraps it, as the wrapper's
    own quoting put them there: shlex writes a quote inside single quotes as
    '"'"', a Windows command line writes one inside double quotes as \\"."""
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("'\"'\"'", "'")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"')
    return text


def _powershell_script(command: str) -> str | None:
    """The script inside a `powershell -NoProfile -Command <script>` wrapper,
    as it was written, quotes and all."""
    spans = _word_spans(command)
    if not spans or PureWindowsPath(spans[0][1]).name.lower() not in _POWERSHELLS:
        return None
    index = 1
    while index < len(spans):
        flag = spans[index][1].lower()
        if flag in _POWERSHELL_FLAGS:
            index += 1
        elif flag in _POWERSHELL_FLAGS_WITH_VALUE:
            index += 2
        elif flag in ("-command", "-c"):
            if index + 1 >= len(spans):
                return None
            # PowerShell joins whatever follows into one script.
            script = command[spans[index + 1][0] :].strip()
            if index + 2 == len(spans):
                script = _unquoted(script)
            prefix = _POWERSHELL_UTF8.match(script)
            return (script[prefix.end() :] if prefix else script).strip() or None
        else:
            return None
    return None


def shell_script(command: str) -> str | None:
    """The shell script inside Codex's `/bin/bash -lc '<script>'` wrapper
    (or its PowerShell equivalent on Windows)."""
    powershell = _powershell_script(command)
    if powershell is not None:
        return powershell
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if len(argv) == 3 and Path(argv[0]).name in _SHELLS and argv[1] in ("-c", "-lc"):
        return argv[2]
    return command


def _plain_read(script: str, base: Path, root: Path) -> bool:
    if any(ch in _SHELL_SYNTAX for ch in script):
        return False
    try:
        argv = shlex.split(script)
    except ValueError:
        return False
    if not argv or argv[0] not in _READ_PROGRAMS:
        return False
    if argv[0] == "find" and any(arg.startswith(_FIND_ACTIONS) for arg in argv):
        return False
    if argv[0] == "rg" and any(arg.startswith("--pre") for arg in argv):
        return False
    paths = [base]
    for arg in argv[1:]:
        if arg.startswith("-"):
            if "=" in arg:
                paths.append(base / arg.split("=", 1)[1])
            continue
        paths.append(base / arg)
    return all(path.resolve().is_relative_to(root) for path in paths)


def _plain_powershell_path(word: str) -> bool:
    """Whether `word` names a file, as the folder check assumes. A colon
    makes it something else unless it follows a single drive letter:
    `env:X`, `HKLM:\\x` and `Cert:\\` are providers (the environment, the
    registry), `file.txt:stream` is a hidden stream, and Python would read
    each as a plain name inside the folder. Device names hang or reach
    hardware."""
    colon = word.find(":")
    if colon not in (-1, 1) or (colon == 1 and not word[0].isalpha()):
        return False
    names = word.replace("\\", "/").split("/")
    return not any(name.split(".")[0].rstrip(" ").upper() in _WINDOWS_DEVICES for name in names)


def _powershell_read(script: str, base: Path, root: Path) -> bool:
    if any(ch in _POWERSHELL_SYNTAX for ch in script):
        return False
    words = _words(script)
    if not words or words[0].lower() not in _POWERSHELL_READS:
        return False
    paths = [base]
    for word in words[1:]:
        if word.startswith("-"):
            # `-Path:value` carries its value; other parameters stand alone
            # and the word after one is looked at as the path it may be.
            value = word.partition(":")[2]
            if value:
                if not _plain_powershell_path(value):
                    return False
                paths.append(base / value.replace("\\", "/"))
            continue
        if not _plain_powershell_path(word):
            return False
        paths.append(base / word.replace("\\", "/"))
    return all(path.resolve().is_relative_to(root) for path in paths)


def reads_only(command: str, cwd: str, folder: Path) -> bool:
    """Whether `command` is one read-only program reading only inside
    `folder` -- optionally after one `cd <dir> &&` into a folder inside it,
    the prefix Codex puts on nearly every command."""
    script = shell_script(command)
    if script is None:
        return False
    root = folder.resolve()
    base = Path(cwd) if cwd else root
    if _powershell_script(command) is not None:
        return _powershell_read(script, base, root)
    prefix = _CD_PREFIX.fullmatch(script)
    if prefix is not None:
        word = prefix["dir"]
        if word[0] not in "\"'" and any(ch in _SHELL_SYNTAX for ch in word):
            return False
        try:
            [target] = shlex.split(word)
        except ValueError:
            return False
        base = base / target
        script = prefix["rest"]
    return _plain_read(script, base, root)


class _Turn:
    """Listener for one turn's notifications and server requests."""

    def __init__(self, decide: Decide, on_event: OnEvent | None, folder: Path) -> None:
        self.folder = folder
        self.decide = decide
        self.on_event = on_event
        self.turn_id: str | None = None
        self.done: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.messages: list[str] = []
        self.usage = Usage()
        self.commands = 0
        self.error: str | None = None
        # Notifications can arrive before turn/start's reply names the turn;
        # they wait here and are handled first, in order.
        self._early: list[tuple[str, dict[str, Any]]] = []
        self.known_id: str | None = None
        self._loop = asyncio.get_running_loop()
        self._last_heard = self._loop.time()
        self._running: set[str] = set()
        self._asking = 0
        self._deciding: set[asyncio.Task[Any]] = set()
        # A file change's approval names only the item; its files come with
        # the item, which starts first.
        self._change_paths: dict[str, tuple[str, ...]] = {}

    def stalled(self, seconds: float) -> bool:
        quiet = self._loop.time() - self._last_heard
        return not self._running and not self._asking and quiet > seconds

    async def _emit(self, kind: str, data: dict[str, Any]) -> None:
        if self.on_event is not None:
            await self.on_event(CodexEvent(kind, data))

    def ours(self, params: dict[str, Any]) -> bool:
        turn_id = params.get("turnId") or (params.get("turn") or {}).get("id")
        return turn_id is None or turn_id == self.known_id

    async def started(self, turn_id: str) -> None:
        self.known_id = turn_id
        # Events that arrive while these are handled join the same list,
        # so nothing overtakes an earlier one.
        while self._early:
            await self._handle(*self._early.pop(0))
        self.turn_id = turn_id

    async def notification(self, method: str, params: dict[str, Any]) -> None:
        self._last_heard = self._loop.time()
        if self.turn_id is None:
            self._early.append((method, params))
            return
        await self._handle(method, params)

    async def _handle(self, method: str, params: dict[str, Any]) -> None:
        if not self.ours(params):
            return
        if method == "item/agentMessage/delta":
            await self._emit("text", {"text": params.get("delta", "")})
        elif method in ("item/started", "item/completed"):
            await self._item(method, params.get("item") or {})
        elif method == "thread/tokenUsage/updated":
            last = (params.get("tokenUsage") or {}).get("last") or {}
            self.usage.add(last)
            await self._emit("usage", dict(last))
        elif method == "error":
            if not params.get("willRetry"):
                self.error = (
                    (params.get("error") or {}).get("message")
                ) or "Codex reported an error."
        elif method == "turn/completed" and not self.done.done():
            self.done.set_result(params.get("turn") or {})

    async def _item(self, method: str, item: dict[str, Any]) -> None:
        kind = item.get("type")
        if kind == "agentMessage" and method == "item/completed":
            text = str(item.get("text") or "")
            self.messages.append(text)
            await self._emit("message", {"text": text})
        elif kind == "commandExecution":
            if method == "item/started":
                self.commands += 1
                self._running.add(str(item.get("id")))
                await self._emit(
                    "command_started", {"id": item.get("id"), "command": item.get("command", "")}
                )
            else:
                self._running.discard(str(item.get("id")))
                await self._emit(
                    "command_finished",
                    {
                        "id": item.get("id"),
                        "command": item.get("command", ""),
                        "status": item.get("status"),
                        "exit_code": item.get("exitCode"),
                        "output": item.get("aggregatedOutput") or "",
                    },
                )
        elif kind == "fileChange" and method == "item/started":
            self._change_paths[str(item.get("id"))] = tuple(
                str(c.get("path")) for c in item.get("changes") or []
            )
        elif kind == "fileChange" and method == "item/completed":
            paths = [str(c.get("path")) for c in item.get("changes") or []]
            await self._emit(
                "file_change", {"id": item.get("id"), "paths": paths, "status": item.get("status")}
            )

    async def server_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "item/commandExecution/requestApproval":
            command = str(params.get("command") or "")
            if reads_only(command, str(params.get("cwd") or ""), self.folder):
                await self._emit("auto_approved", {"command": command})
                return {"decision": "accept"}
            request = ApprovalRequest(
                kind="command",
                item_id=str(params.get("itemId", "")),
                command=str(params.get("command") or ""),
                cwd=str(params.get("cwd") or ""),
                reason=str(params.get("reason") or ""),
            )
        elif method == "item/fileChange/requestApproval":
            item_id = str(params.get("itemId", ""))
            request = ApprovalRequest(
                kind="file_change",
                item_id=item_id,
                reason=str(params.get("reason") or ""),
                paths=self._change_paths.get(item_id, ()),
            )
        else:
            # Tool calls, user-input prompts, MCP elicitations, permission
            # grants: none is offered to this thread yet.
            raise RequestFailed(method, {"message": f"coscribe doesn't handle {method}"})
        self._asking += 1
        task = asyncio.current_task()
        if task is not None:
            self._deciding.add(task)
        try:
            approved = await self.decide(request)
        finally:
            self._asking -= 1
            self._deciding.discard(task)
            self._last_heard = self._loop.time()
        return {"decision": "accept" if approved else "decline"}

    def abandon_requests(self) -> None:
        """The turn is over: an approval still waiting on a person has
        nothing left to approve, and must not stay asking."""
        for task in list(self._deciding):
            task.cancel()

    def closed(self, error: AppServerClosed) -> None:
        if not self.done.done():
            self.done.set_exception(error)


class CodexThread:
    def __init__(
        self,
        host: CodexHost,
        model: CodexModel,
        cwd: Path,
        *,
        thread_id: str | None = None,
        developer_instructions: str = "",
        context_window: int | None = None,
        stall_seconds: float = STALL_SECONDS,
        fallback_context: str = "",
        on_open: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._host = host
        self._model = model
        self._cwd = Path(cwd)
        self.thread_id = thread_id
        self._instructions = developer_instructions
        self._context_window = context_window
        self._stall_seconds = stall_seconds
        # What a thread that couldn't be picked up again is told first, in
        # place of the history it no longer has.
        self._fallback_context = fallback_context
        self._on_open = on_open
        self.resumed = False

    def _thread_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self._model.model,
            "modelProvider": self._model.provider_id,
            "cwd": str(self._cwd),
            "approvalPolicy": "untrusted",
            "sandbox": "danger-full-access",
        }
        if self._context_window:
            params["config"] = {"model_context_window": self._context_window}
        return params

    async def _open(self, server: AppServer) -> str:
        result = None
        if self.thread_id is not None:
            try:
                result = await server.request(
                    "thread/resume", {"threadId": self.thread_id, **self._thread_params()}
                )
                self.resumed = True
            except RequestFailed:
                # Codex writes a thread down only once a turn of it ran; one
                # whose first turn never got that far, or whose record is
                # gone, can't be resumed, and carries on as a new thread.
                logger.warning("Codex thread %s can't be resumed", self.thread_id, exc_info=True)
        if result is None:
            params = self._thread_params()
            if self._instructions:
                params["developerInstructions"] = self._instructions
            result = await server.request("thread/start", params)
        self.thread_id = str(result["thread"]["id"])
        return self.thread_id

    async def run_turn(
        self,
        prompt: str,
        decide: Decide,
        on_event: OnEvent | None = None,
        images: Sequence[str] = (),
    ) -> TurnResult:
        """`images` are URLs, data: URLs included."""
        before = await asyncio.to_thread(_snapshot, self._cwd)
        async with self._host.use() as server:
            thread_id = await self._open(server)
            if self._on_open is not None:
                await self._on_open(thread_id)
            if self._fallback_context and not self.resumed:
                prompt = f"{self._fallback_context}\n\n{prompt}"
            turn = _Turn(decide, on_event, self._cwd)
            server.listen(thread_id, turn)
            try:
                start = asyncio.ensure_future(
                    server.request(
                        "turn/start",
                        {
                            "threadId": thread_id,
                            "input": [
                                {"type": "text", "text": prompt, "text_elements": []},
                                *({"type": "image", "url": url} for url in images),
                            ],
                        },
                    )
                )
                try:
                    reply = await asyncio.shield(start)
                    await turn.started(str(reply["turn"]["id"]))
                    final = await self._wait(server, turn)
                except asyncio.CancelledError:
                    # Codex may already be running the turn: it has to be
                    # told to stop, which needs the turn's id.
                    with contextlib.suppress(Exception):
                        reply = await asyncio.wait_for(start, _INTERRUPT_SECONDS)
                        if turn.known_id is None:
                            await turn.started(str(reply["turn"]["id"]))
                    await self._interrupt(server, turn)
                    raise
            except AppServerClosed as exc:
                return TurnResult(
                    "failed",
                    turn.messages[-1] if turn.messages else "",
                    error=str(exc),
                    usage=turn.usage,
                )
            finally:
                turn.abandon_requests()
                server.unlisten(thread_id)
        after = await asyncio.to_thread(_snapshot, self._cwd)
        status = _STATUSES.get(str(final.get("status")), "failed")
        error = (final.get("error") or {}).get("message") or turn.error
        return TurnResult(
            status=status,
            text=turn.messages[-1] if turn.messages else "",
            error=None if status == "completed" else error or "The turn didn't finish.",
            usage=turn.usage,
            files_changed=changed_files(before, after),
            commands=turn.commands,
        )

    async def _wait(self, server: AppServer, turn: _Turn) -> dict[str, Any]:
        check = min(self._stall_seconds, 5.0)
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(turn.done), check)
            except TimeoutError:
                if turn.stalled(self._stall_seconds):
                    await self._interrupt(server, turn)
                    message = (
                        f"Codex sent nothing for {self._stall_seconds:.0f} seconds, so the "
                        "turn was stopped. The model's connection may be stuck."
                    )
                    return {"status": "failed", "error": {"message": message}}

    async def _interrupt(self, server: AppServer, turn: _Turn) -> None:
        if turn.known_id is None or self.thread_id is None:
            return
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                server.request(
                    "turn/interrupt", {"threadId": self.thread_id, "turnId": turn.known_id}
                ),
                _INTERRUPT_SECONDS,
            )
            await asyncio.wait_for(asyncio.shield(turn.done), _INTERRUPT_SECONDS)
        # turn/interrupt leaves the commands the turn started running; only
        # this ends them.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                server.request("thread/backgroundTerminals/clean", {"threadId": self.thread_id}),
                _INTERRUPT_SECONDS,
            )
