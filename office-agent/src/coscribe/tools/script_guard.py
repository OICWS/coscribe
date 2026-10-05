"""A guard rail around `run_python_script`/`run_node_script`: a script may
only write inside the workspace and the folders the conversation added --
the same folders the file tools are held to.

**Not a security boundary.** A script can still launch programs, and
Python code that calls the operating system directly gets past the hook
below. It turns a model's mistake (saving to the wrong place, tidying the
wrong folder) into a refusal that says which folder to add, instead of a
file in a place nobody looks. Reads are left open: libraries, fonts and
the user's own input files live all over the disk.

Python has no permission model of its own, so the guard is an audit hook
installed by a small wrapper (kept here as source, written beside the
script: a frozen build has no .py files to point at). Node has one built
in (`--permission`), used when the installed Node is new enough.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

_BLOCKED_MARK = "COSCRIBE_BLOCKED_WRITE:"

PYTHON_WRAPPER = r'''
import atexit, json, os, runpy, sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
script = sys.argv[2]
roots = [os.path.normcase(os.path.realpath(p)) for p in config["write"]]
blocked = []
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
NULL_DEVICES = {"nul", os.devnull.lower()}


def check(path):
    if path is None or isinstance(path, int):
        return
    try:
        path = os.fsdecode(path)
    except TypeError:
        return
    if path.lower() in NULL_DEVICES:
        return
    real = os.path.normcase(os.path.realpath(path))
    if any(real == root or real.startswith(root + os.sep) for root in roots):
        return
    blocked.append(path)
    raise PermissionError(
        "coscribe: writing outside the allowed folders is blocked: " + path
    )


def hook(event, args):
    if event == "open":
        if args[2] & WRITE_FLAGS:
            check(args[0])
    elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.truncate"):
        check(args[0])
    elif event == "os.rename":
        check(args[0])
        check(args[1])
    elif event in ("os.symlink", "os.link"):
        check(args[1])


def report():
    # A script often catches the refusal and prints its own message, so this
    # runs however it ended. The interpreter's own files are not the user's.
    own = [os.path.normcase(os.path.realpath(p)) for p in {sys.prefix, sys.base_prefix}]
    for path in blocked:
        real = os.path.normcase(os.path.realpath(path))
        if path.endswith(".pyc") or any(real.startswith(p + os.sep) for p in own):
            continue
        sys.stderr.write("@@MARK@@" + path + "\n")
        return


sys.dont_write_bytecode = True
atexit.register(report)
sys.addaudithook(hook)
sys.argv = [script]
runpy.run_path(script, run_name="__main__")
'''.replace("@@MARK@@", _BLOCKED_MARK)


def writable_roots(workspace_root: Path, extra_writable: list[Path]) -> list[str]:
    """Where a script may write: the workspace, the added folders, and the
    temp folder (libraries and the script's own scratch files use it)."""
    folders = [workspace_root, *extra_writable, Path(tempfile.gettempdir())]
    return list(dict.fromkeys(str(p.expanduser().resolve()) for p in folders))


def write_python_wrapper(scratch_dir: Path, roots: list[str]) -> tuple[Path, Path]:
    """(wrapper path, config path) inside `scratch_dir`."""
    config = scratch_dir / "guard.json"
    config.write_text(json.dumps({"write": roots}), encoding="utf-8")
    wrapper = scratch_dir / "guard_runner.py"
    wrapper.write_text(PYTHON_WRAPPER, encoding="utf-8")
    return wrapper, config


_NODE_FLAG_SETS = (
    # Newest spelling first; older Nodes call the switch experimental and
    # know fewer of the allowances.
    ("--permission", "--allow-addons"),
    ("--permission",),
    # Node 20 prints an ExperimentalWarning and two SecurityWarnings on every
    # run under these flags, which would land in every script's stderr.
    # --disable-warning arrived in 20.11; earlier Nodes reject it and fall
    # through to the next set.
    (
        "--experimental-permission",
        "--disable-warning=ExperimentalWarning",
        "--disable-warning=SecurityWarning",
    ),
    ("--experimental-permission",),
)
_NODE_ALLOWANCES = ("--allow-fs-read=*", "--allow-child-process", "--allow-worker")
_node_guard_flags: dict[str, tuple[str, ...] | None] = {}


def _probe_node_flags(node: str) -> tuple[str, ...] | None:
    for switches in _NODE_FLAG_SETS:
        flags = (*switches, *_NODE_ALLOWANCES)
        try:
            probe = subprocess.run(
                [node, *flags, "-e", ""], capture_output=True, timeout=20, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if probe.returncode == 0:
            return flags
    return None


def node_guard_args(node: str, roots: list[str]) -> list[str] | None:
    """Node's own flags limiting writes to `roots`, or None when this Node
    has no permission model (the script then runs unguarded)."""
    if node not in _node_guard_flags:
        _node_guard_flags[node] = _probe_node_flags(node)
    flags = _node_guard_flags[node]
    if flags is None:
        return None
    return [*flags, *(f"--allow-fs-write={root}" for root in roots)]


_NODE_DENIED = re.compile(
    r"permission: 'FileSystemWrite',\s*resource: '((?:[^'\\]|\\.)*)'", re.DOTALL
)


def blocked_write(stderr: str) -> tuple[str | None, str]:
    """(the path a script was stopped from writing, `stderr` without the
    guard's own marker line)."""
    path: str | None = None
    kept: list[str] = []
    for line in stderr.splitlines(keepends=True):
        if line.startswith(_BLOCKED_MARK):
            path = path or line[len(_BLOCKED_MARK) :].strip()
        else:
            kept.append(line)
    clean = "".join(kept)
    if path is None:
        found = _NODE_DENIED.search(clean)
        if found:
            path = found.group(1).replace("\\\\", "\\")
    return path, clean


_NODE_GUARD_WARNING = re.compile(
    r"^\(node:\d+\) SecurityWarning: The flag --allow-.*\n"
    r"|^\(node:\d+\) ExperimentalWarning: Permission is an experimental feature.*\n"
    r"|^\(Use `node --trace-warnings.*\n",
    re.MULTILINE,
)


def without_guard_warnings(stderr: str) -> str:
    """Node prints a warning for each `--allow-*` flag it was given, and
    Nodes before 22 one more for the experimental permission switch; they
    say nothing about the script."""
    return _NODE_GUARD_WARNING.sub("", stderr)


def suggested_folder(blocked: str) -> str | None:
    """The folder to offer adding for a refused write: the nearest one that
    exists, unless that is a whole drive or the user's home -- too much to
    hand over with one click."""
    path = Path(blocked)
    for candidate in (path, *path.parents):
        if candidate.is_dir():
            if candidate == Path(candidate.anchor) or candidate == Path.home():
                return None
            return str(candidate)
    return None


def refusal_note(path: str) -> str:
    return (
        f'\n[coscribe] The script was stopped from writing "{path}": it is outside the '
        "workspace and the folders added to this conversation. Write inside the workspace "
        "instead, or ask the user to add that folder (the chat shows an Add folder button)."
    )


_LOCKED_FILE_ERROR = re.compile(
    r"PermissionError: \[(?:Errno 13|WinError (?:5|32|33))\][^\n]*?['\"]([^'\"\n]+)['\"]"
)


def locked_file_note(stderr: str) -> str:
    """A script that couldn't write a file because Excel/Word has it open
    ends in a PermissionError; say what that usually means so the model
    asks the user instead of chmod-ing or saving somewhere else."""
    match = _LOCKED_FILE_ERROR.search(stderr)
    if match is None:
        return ""
    name = Path(match.group(1)).name
    return (
        f'\n[coscribe] "{name}" could not be written: it is probably open in another '
        "program (Excel, Word, a PDF viewer) or read-only. Ask the user to close it and "
        "try again; don't save a copy under another name unless they say so."
    )
