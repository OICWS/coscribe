"""Local filesystem tools, scoped to a single workspace root.

Plain-text file I/O. Word/PDF documents have their own toolkit
(tools/documents.py); everything else (browser automation, email, ...) is
meant to arrive through MCP servers or Skills the user configures. See
ARCHITECTURE.md.
"""

from __future__ import annotations

import difflib
import re
import shutil
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from ..runtime.types import tool_metadata
from ._workspace import WorkspaceScope

DEFAULT_IGNORES = {".git", "__pycache__", ".venv", "node_modules"}


def _line_diff_stat(old_text: str, new_text: str) -> tuple[int, int]:
    """Line-level (added, removed) counts between two text blobs, via
    SequenceMatcher opcodes -- not a full unified diff, just the two totals
    the transcript's own "+N -M" badge needs (see transcriptGrouping.ts's
    diffStatOf). A `replace` opcode counts as removing its old span and
    adding its new one, same as `git diff --stat`'s own line-counting
    convention (a changed line is one removed + one added, not a wash)."""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    added = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            removed += i2 - i1
            added += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "insert":
            added += j2 - j1
    return added, removed


class FileToolkit:
    def __init__(
        self,
        root: str | Path,
        *,
        extra_readable: Sequence[str | Path] = (),
        extra_writable: Sequence[str | Path] = (),
    ) -> None:
        self._scope = WorkspaceScope(
            root, extra_readable=extra_readable, extra_writable=extra_writable
        )

    @property
    def root(self) -> Path:
        return self._scope.root

    def _resolve(self, path: str, *, write: bool = False) -> Path:
        return self._scope.resolve(path, write=write)

    def _relative(self, path: Path) -> str:
        return self._scope.relative(path)

    def _ignored(self, path: Path) -> bool:
        # DEFAULT_IGNORES only makes sense relative to *some* allowed root
        # (whichever one the path is actually under) -- a path outside all
        # of them never reaches here, since _resolve() already rejected it.
        relative = self._relative(path)
        return any(part in DEFAULT_IGNORES for part in Path(relative).parts)

    def list_files(
        self, path: str = ".", pattern: str = "*", recursive: bool = True
    ) -> dict[str, object]:
        """List files under the workspace, optionally filtered by glob pattern."""
        base = self._resolve(path)
        if not base.exists():
            raise ValueError(f"Path does not exist: {path}")
        if not base.is_dir():
            raise ValueError(f"Path is not a directory: {path}")
        iterator = base.rglob(pattern) if recursive else base.glob(pattern)
        results = sorted(
            self._relative(item) for item in iterator if item.is_file() and not self._ignored(item)
        )
        return {
            "files": results[:200],
            "truncated": len(results) > 200,
            "total_count": len(results),
        }

    def read_file(
        self, path: str, head: Optional[int] = None, tail: Optional[int] = None  # noqa: UP045
    ) -> str:
        """Read the UTF-8 text contents of a file under the workspace, in
        full or (via head/tail) just its first/last N lines."""
        if head is not None and tail is not None:
            raise ValueError("head and tail cannot both be given -- pick one.")
        file_path = self._resolve(path)
        if not file_path.exists():
            raise ValueError(f"File does not exist: {path}")
        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {path}")
        content = file_path.read_text(encoding="utf-8")
        if head is not None:
            return "".join(content.splitlines(keepends=True)[:head])
        if tail is not None:
            return "".join(content.splitlines(keepends=True)[-tail:] if tail > 0 else [])
        return content

    def get_file_info(self, path: str) -> dict[str, object]:
        """Metadata for one file or directory under the workspace -- size
        and last-modified time, without reading its contents."""
        file_path = self._resolve(path)
        if not file_path.exists():
            raise ValueError(f"Path does not exist: {path}")
        stat = file_path.stat()
        is_dir = file_path.is_dir()
        return {
            "path": self._relative(file_path),
            "is_directory": is_dir,
            # A directory's own st_size is filesystem block-allocation
            # bookkeeping, not the size of its contents -- reporting it
            # would be misleading, so it's omitted rather than shown as a
            # number that looks meaningful but isn't.
            "size_bytes": None if is_dir else stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        }

    def search_files(
        self, query: str, path: str = ".", pattern: str = "*", regex: bool = False
    ) -> dict[str, object]:
        """Search for a literal substring, or (with regex=True) a regular
        expression, across text files under the workspace."""
        base = self._resolve(path)
        if not base.exists():
            raise ValueError(f"Path does not exist: {path}")
        compiled: re.Pattern[str] | None = None
        if regex:
            try:
                compiled = re.compile(query)
            except re.error as exc:
                raise ValueError(f"Invalid regex {query!r}: {exc}") from exc
        matches: list[dict[str, object]] = []
        truncated = False
        for item in base.rglob(pattern):
            if not item.is_file() or self._ignored(item):
                continue
            try:
                text = item.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                hit = compiled.search(line) if compiled else query in line
                if hit:
                    matches.append(
                        {"path": self._relative(item), "line": line_number, "text": line}
                    )
                if len(matches) >= 100:
                    truncated = True
                    break
            if truncated:
                break
        return {"matches": matches, "truncated": truncated}

    def write_file(self, path: str, content: str, overwrite: bool = True) -> dict[str, object]:
        """Write UTF-8 text content to a file under the workspace, creating parent dirs."""
        file_path = self._resolve(path, write=True)
        if file_path.exists() and file_path.is_dir():
            raise ValueError(f"Path is a directory: {path}")
        if file_path.exists() and not overwrite:
            raise FileExistsError(f"File already exists: {path}")
        old_content = ""
        if file_path.exists():
            try:
                old_content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # The file being replaced isn't UTF-8 text (e.g. a stray
                # binary at this path) -- write_file itself only ever
                # produces UTF-8 text, so there's no meaningful line diff
                # against it; treat the whole new content as added rather
                # than failing a write that would have succeeded before
                # this diff-stat feature existed.
                pass
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        added, removed = _line_diff_stat(old_content, content)
        return {
            "path": self._relative(file_path),
            "bytes_written": len(content.encode("utf-8")),
            "lines_added": added,
            "lines_removed": removed,
        }

    def edit_file(
        self, path: str, old_text: str, new_text: str, replace_all: bool = False
    ) -> dict[str, object]:
        """Replace an exact substring in a text file under the workspace,
        without rewriting the rest of it."""
        if not old_text:
            raise ValueError("old_text cannot be empty.")
        if old_text == new_text:
            raise ValueError("old_text and new_text are identical -- nothing to change.")
        file_path = self._resolve(path, write=True)
        if not file_path.exists():
            raise ValueError(f"File does not exist: {path}")
        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {path}")
        content = file_path.read_text(encoding="utf-8")
        occurrences = content.count(old_text)
        if occurrences == 0:
            raise ValueError(f"old_text not found in {path}.")
        if occurrences > 1 and not replace_all:
            raise ValueError(
                f"old_text appears {occurrences} times in {path} -- ambiguous which one "
                "to replace. Include more surrounding context to make old_text unique, "
                "or pass replace_all=True to replace every occurrence."
            )
        new_content = content.replace(old_text, new_text)
        file_path.write_text(new_content, encoding="utf-8")
        added, removed = _line_diff_stat(content, new_content)
        return {
            "path": self._relative(file_path),
            "replacements": occurrences if replace_all else 1,
            "lines_added": added,
            "lines_removed": removed,
        }

    def edit_file_batch(self, path: str, edits: str) -> dict[str, object]:
        """Apply several edit_file-style (old_text, new_text) edits to one
        file in a single call -- ROADMAP.md's Phase 7 item 4. `edits` is
        one string, alternating old_text/new_text chunks separated by a
        line containing only `---` (the exact same delimiter convention
        write_pptx's own `_slide_chunks` already uses for its
        `---`-separated slide content) -- not a `list[dict]`/`list[str]`
        structured parameter: **found the hard way** that this
        codebase's tool schemas have to stay Gemini-compatible (see
        test_coordinator.py's test_all_tool_schemas_are_gemini_compatible)
        and aisuite's Tools.__infer_from_signature has no handling at all
        for list/dict-typed parameters -- not just nested ones -- falling
        back to a broken literal type string for any of them. A single
        `str` parameter is the only shape guaranteed to produce a valid
        JSON Schema "string" type through that schema builder, still live
        today (backs /api/tools, the Settings tools tab) even though real
        conversations run through the separate langgraph/LangChain path.

        Edits apply in order, each against the result of the ones before
        it (so a later old_text chunk can be text an earlier new_text
        chunk just introduced), but nothing is written to disk until
        every edit has been validated -- one bad edit anywhere in the
        batch leaves the file completely untouched, same "raise, don't
        silently do nothing" discipline as edit_file itself.

        Deliberately simpler than edit_file in one way: no per-edit
        replace_all -- every old_text chunk must already be unique in the
        file at the point it's applied. If one specific change genuinely
        needs replace_all, make that one change with a separate edit_file
        call instead."""
        chunks = [chunk for chunk in edits.split("\n---\n") if chunk.strip()]
        if len(chunks) < 2:
            raise ValueError(
                "edits needs at least one old_text/new_text pair, separated by a "
                "line containing only '---' -- e.g. 'old text\\n---\\nnew text'."
            )
        if len(chunks) % 2 != 0:
            raise ValueError(
                f"edits has {len(chunks)} '---'-separated chunks, an odd number -- "
                "each old_text chunk needs a matching new_text chunk right after it."
            )
        file_path = self._resolve(path, write=True)
        if not file_path.exists():
            raise ValueError(f"File does not exist: {path}")
        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {path}")
        original_content = file_path.read_text(encoding="utf-8")
        content = original_content

        pairs = list(zip(chunks[0::2], chunks[1::2], strict=True))
        for index, (old_text, new_text) in enumerate(pairs):
            if old_text == new_text:
                raise ValueError(
                    f"edit {index}: old_text and new_text are identical -- nothing to change."
                )
            occurrences = content.count(old_text)
            if occurrences == 0:
                raise ValueError(
                    f"edit {index}'s old_text not found in {path} (checked against the "
                    "result of any earlier edits in this batch, if any)."
                )
            if occurrences > 1:
                raise ValueError(
                    f"edit {index}'s old_text appears {occurrences} times in {path} -- "
                    "ambiguous which one to replace. Include more surrounding context to "
                    "make it unique, or make this one change with a separate edit_file "
                    "call using replace_all=True."
                )
            content = content.replace(old_text, new_text)

        file_path.write_text(content, encoding="utf-8")
        added, removed = _line_diff_stat(original_content, content)
        return {
            "path": self._relative(file_path),
            "edits_applied": len(pairs),
            "lines_added": added,
            "lines_removed": removed,
        }

    def delete_file(self, path: str) -> dict[str, object]:
        """Permanently delete one file under the workspace. Directories are
        rejected -- not the "rm -rf" this tool sounds like -- use
        run_python_script (shutil.rmtree) for a whole directory."""
        file_path = self._resolve(path, write=True)
        if not file_path.exists():
            raise ValueError(f"File does not exist: {path}")
        if file_path.is_dir():
            raise ValueError(
                f"Path is a directory: {path} -- delete_file only removes a single "
                "file; use run_python_script for a whole directory."
            )
        file_path.unlink()
        return {"path": self._relative(file_path), "deleted": True}

    def move_file(
        self, source: str, destination: str, overwrite: bool = False
    ) -> dict[str, object]:
        """Move or rename a file or directory under the workspace."""
        source_path = self._resolve(source, write=True)
        dest_path = self._resolve(destination, write=True)
        if not source_path.exists():
            raise ValueError(f"Source does not exist: {source}")
        if dest_path.exists():
            if dest_path.is_dir():
                # shutil.move's own behavior for an existing directory
                # destination is to move source *inside* it (like `mv` into
                # a dir), not replace it -- surprising either way overwrite
                # is set, so this is always rejected rather than guessing
                # which behavior was meant.
                raise ValueError(
                    f"Destination already exists and is a directory: {destination} "
                    "-- move_file won't merge into or replace an existing "
                    "directory; use run_python_script for that."
                )
            if not overwrite:
                raise FileExistsError(f"Destination already exists: {destination}")
            dest_path.unlink()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(dest_path))
        return {
            "source": self._relative(source_path),
            "destination": self._relative(dest_path),
        }

    def copy_file(
        self, source: str, destination: str, overwrite: bool = False
    ) -> dict[str, object]:
        """Copy one file under the workspace, byte-for-byte (unlike write_file's
        UTF-8-text-only shape, this works for binary files too -- images,
        pdfs, any format). Directories are rejected -- use run_python_script
        (shutil.copytree) for a whole directory."""
        source_path = self._resolve(source, write=False)
        dest_path = self._resolve(destination, write=True)
        if not source_path.exists():
            raise ValueError(f"Source does not exist: {source}")
        if source_path.is_dir():
            raise ValueError(
                f"Source is a directory: {source} -- copy_file only copies a "
                "single file; use run_python_script for a whole directory."
            )
        if dest_path.exists() and not overwrite:
            raise FileExistsError(f"Destination already exists: {destination}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source_path), str(dest_path))
        return {
            "source": self._relative(source_path),
            "destination": self._relative(dest_path),
        }


def build_file_tools(
    root: str | Path,
    *,
    extra_readable: Sequence[str | Path] = (),
    extra_writable: Sequence[str | Path] = (),
) -> list[Callable[..., Any]]:
    """Return the tool callables the Coordinator agent can call, bound to `root`
    (plus any user-configured extra_readable/extra_writable directories --
    COSCRIBE_EXTRA_READABLE_DIRS/COSCRIBE_EXTRA_WRITABLE_DIRS)."""
    toolkit = FileToolkit(root, extra_readable=extra_readable, extra_writable=extra_writable)

    def list_files(
        path: str = ".", pattern: str = "*", recursive: bool = True
    ) -> dict[str, object]:
        """List files under the workspace. An absolute path outside the
        workspace root is also allowed if it falls under a directory the
        user has explicitly configured as extra-readable/extra-writable.

        Returns `{"files": [...], "truncated": bool, "total_count": int}` --
        `files` is capped at 200 entries; if `truncated` is true, `files` is
        only the first 200 of `total_count` real matches, not the whole
        list. Narrow `path` and/or `pattern` (e.g. a specific subdirectory,
        or "*.pdf" instead of "*") and call again to see the rest, rather
        than assuming `files` is complete.

        Args:
            path: directory to list, relative to the workspace root (or an
                allowed absolute path)
            pattern: glob pattern to filter by, e.g. "*.txt"
            recursive: whether to descend into subdirectories
        """
        return toolkit.list_files(path=path, pattern=pattern, recursive=recursive)

    def read_file(
        path: str, head: Optional[int] = None, tail: Optional[int] = None  # noqa: UP045
    ) -> str:
        """Read the text contents of a file under the workspace. An
        absolute path outside the workspace root is also allowed if it
        falls under a configured extra-readable/extra-writable directory.

        With neither `head` nor `tail`, returns the whole file (as before).
        Pass one (not both) to read only that many lines from the start or
        end -- for a quick look at a large file without spending the
        context a full read would, or before deciding whether to read the
        whole thing.

        Args:
            path: file to read, relative to the workspace root (or an
                allowed absolute path)
            head: if given, only the first this-many lines
            tail: if given, only the last this-many lines (mutually
                exclusive with head)
        """
        return toolkit.read_file(path=path, head=head, tail=tail)

    def get_file_info(path: str) -> dict[str, object]:
        """Metadata for one file or directory under the workspace (or a
        configured extra-readable/extra-writable directory) -- its size and
        last-modified time, without reading its contents. Useful before
        read_file on a file that might be large, or to tell which of a few
        candidate files is newest.

        Args:
            path: file or directory to inspect, relative to the workspace
                root (or an allowed absolute path)
        """
        return toolkit.get_file_info(path=path)

    def search_files(
        query: str, path: str = ".", pattern: str = "*", regex: bool = False
    ) -> dict[str, object]:
        """Search text files under the workspace, line by line -- a literal
        substring by default, or (regex=True) a Python regular expression
        for anything a plain substring can't express (alternation,
        anchors, character classes, capturing a variable name pattern,
        etc.). An absolute path outside the workspace root is also allowed
        if it falls under a configured extra directory.

        Returns `{"matches": [...], "truncated": bool}`, each match
        `{"path", "line", "text"}` (the whole matching line, not just the
        matched span). Stops scanning as soon as it finds 100 matches, for
        the same reason `list_files` caps at 200 -- don't assume `matches`
        is a complete result when `truncated` is true; if `truncated` is
        true, `matches`'s real total count is unknown (the search stopped
        early), not necessarily 100. Narrow `path`/`pattern` and call again
        for the rest.

        With `regex=True`, an invalid pattern raises immediately with the
        underlying regex error rather than matching nothing silently.
        Case-insensitive matching is `(?i)` at the start of `query` (plain
        Python `re` syntax), not a separate flag. This tool only finds
        matches -- to change what it finds, follow up with `edit_file`
        (exact-text replace) or, for a bulk regex-based rewrite across many
        matches, `run_python_script` (`re.sub`).

        Args:
            query: substring to search for, or (regex=True) a regular
                expression
            path: directory to search under, relative to the workspace root
                (or an allowed absolute path)
            pattern: glob pattern to filter files by, e.g. "*.txt"
            regex: treat query as a Python regular expression instead of a
                literal substring
        """
        return toolkit.search_files(query=query, path=path, pattern=pattern, regex=regex)

    def write_file(path: str, content: str, overwrite: bool = True) -> dict[str, object]:
        """Write text content to a file under the workspace, creating
        parent directories. An absolute path outside the workspace root is
        also allowed if it falls under a configured extra-writable
        directory (read-only extra directories reject writes).

        Args:
            path: file to write, relative to the workspace root (or an
                allowed writable absolute path)
            content: full text content to write
            overwrite: whether to replace the file if it already exists
        """
        return toolkit.write_file(path=path, content=content, overwrite=overwrite)

    def edit_file(
        path: str, old_text: str, new_text: str, replace_all: bool = False
    ) -> dict[str, object]:
        """Replace an exact substring in a text file under the workspace (or
        a configured extra-writable directory), without rewriting the rest
        of the file -- the tool to reach for instead of read_file + write_file
        when only part of a file needs to change, especially a large one
        where resending the whole thing wastes context.

        `old_text` must match exactly (including whitespace/indentation) and
        must be unique in the file, unless `replace_all=True` -- if it
        appears more than once, this raises rather than guessing which one
        you meant; include more surrounding context in `old_text` to make it
        unique instead. If `old_text` doesn't appear at all, this raises
        too, rather than silently doing nothing.

        Making several separate changes to the same file? Call
        edit_file_batch instead of several edit_file calls in a row -- one
        atomic write instead of several round trips.

        Args:
            path: file to edit, relative to the workspace root (or an
                allowed writable absolute path)
            old_text: exact text to find (must be unique unless replace_all)
            new_text: text to replace it with
            replace_all: replace every occurrence of old_text instead of
                requiring exactly one
        """
        return toolkit.edit_file(
            path=path, old_text=old_text, new_text=new_text, replace_all=replace_all
        )

    def edit_file_batch(path: str, edits: str) -> dict[str, object]:
        """Apply several edit_file-style changes to one file in a single
        call, instead of several separate edit_file calls -- one atomic
        write, and each edit can build on the text an earlier one in the
        same batch just introduced. Nothing is written to disk until every
        edit has been validated; one bad edit anywhere in the batch leaves
        the file completely untouched, same as edit_file's own "raise,
        don't silently do nothing" behavior.

        `edits` is one string: alternating old_text/new_text chunks, each
        pair separated by a line containing only `---` (the same
        delimiter convention write_pptx uses for its own `---`-separated
        slide content) -- e.g. for two edits:
        "first old text\n---\nfirst new text\n---\nsecond old text\n---\nsecond new text".
        Each old_text chunk must match exactly and be unique in the file
        at the point that edit applies (checked against the result of any
        earlier edits in this same batch). Unlike edit_file, there's no
        per-edit `replace_all` -- if one specific change genuinely needs
        to replace every occurrence, make that one change with a separate
        edit_file call instead.

        Args:
            path: file to edit, relative to the workspace root (or an
                allowed writable absolute path)
            edits: alternating old_text/new_text chunks separated by a
                line containing only '---'
        """
        return toolkit.edit_file_batch(path=path, edits=edits)

    def delete_file(path: str) -> dict[str, object]:
        """Permanently delete one file under the workspace (or a configured
        extra-writable directory). There is no undo. Directories are
        rejected -- this is not "rm -rf": reach for run_python_script
        (shutil.rmtree) for a whole directory.

        Args:
            path: file to delete, relative to the workspace root (or an
                allowed writable absolute path)
        """
        return toolkit.delete_file(path=path)

    def move_file(source: str, destination: str, overwrite: bool = False) -> dict[str, object]:
        """Move or rename a file or directory under the workspace (or a
        configured extra-writable directory). Works across directories, not
        just a rename in place. `destination` must not already exist unless
        `overwrite=True` -- and even then, an existing *directory* at
        `destination` is always rejected (moving into vs. replacing it is
        ambiguous either way overwrite is set); use run_python_script for
        that case.

        Args:
            source: file or directory to move, relative to the workspace
                root (or an allowed writable absolute path)
            destination: where to move it to, same path rules as source
            overwrite: replace an existing file at destination (never an
                existing directory -- see above)
        """
        return toolkit.move_file(source=source, destination=destination, overwrite=overwrite)

    def copy_file(source: str, destination: str, overwrite: bool = False) -> dict[str, object]:
        """Copy one file under the workspace (or a configured extra-writable
        directory), byte-for-byte -- unlike write_file/read_file's UTF-8-
        text-only shape, this works for binary files too (images, pdfs, any
        format). Directories are rejected -- reach for run_python_script
        (shutil.copytree) for a whole directory.

        Args:
            source: file to copy, relative to the workspace root (or an
                allowed readable/writable absolute path)
            destination: where to copy it to, same path rules as source
            overwrite: replace an existing file at destination
        """
        return toolkit.copy_file(source=source, destination=destination, overwrite=overwrite)

    return [
        tool_metadata(list_files, risk_category="READ", category="filesystem"),
        tool_metadata(read_file, risk_category="READ", category="filesystem"),
        tool_metadata(get_file_info, risk_category="READ", category="filesystem"),
        tool_metadata(search_files, risk_category="READ", category="filesystem"),
        tool_metadata(write_file, risk_category="WRITE_LOCAL", category="filesystem"),
        tool_metadata(edit_file, risk_category="WRITE_LOCAL", category="filesystem"),
        tool_metadata(edit_file_batch, risk_category="WRITE_LOCAL", category="filesystem"),
        tool_metadata(delete_file, risk_category="WRITE_LOCAL", category="filesystem"),
        tool_metadata(move_file, risk_category="WRITE_LOCAL", category="filesystem"),
        tool_metadata(copy_file, risk_category="WRITE_LOCAL", category="filesystem"),
    ]
