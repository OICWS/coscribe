"""Shared workspace-root path scoping, used by any toolkit that reads or
writes files under a confined root (files, documents, ...).

Beyond the single primary root, optionally accepts extra directories the
user has explicitly trusted -- readable-only or readable+writable -- so a
file that lands outside the workspace (e.g. a browser download folder) can
be read/edited in place instead of needing to be copied into the workspace
first. Writable implies readable. Unconfigured (the default), behavior is
identical to before this existed: everything is confined to `root`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


class WorkspaceScope:
    def __init__(
        self,
        root: str | Path,
        *,
        extra_readable: Sequence[str | Path] = (),
        extra_writable: Sequence[str | Path] = (),
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._extra_writable = [Path(p).expanduser().resolve() for p in extra_writable]
        self._extra_readable = [
            Path(p).expanduser().resolve() for p in extra_readable
        ] + self._extra_writable

    def resolve(self, path: str, *, write: bool = False) -> Path:
        candidate = Path(path).expanduser()
        resolved = (
            candidate.resolve() if candidate.is_absolute() else (self.root / candidate).resolve()
        )
        if self._under(resolved, self.root):
            return resolved
        allowed = self._extra_writable if write else self._extra_readable
        for extra_root in allowed:
            if self._under(resolved, extra_root):
                return resolved
        verb = "written to" if write else "read"
        raise PermissionError(f"Path cannot be {verb} -- outside any allowed directory: {path}")

    def relative(self, path: Path) -> str:
        for candidate_root in (self.root, *self._extra_readable):
            try:
                return path.relative_to(candidate_root).as_posix()
            except ValueError:
                continue
        # Only reachable for a path that never went through resolve() (e.g.
        # a caller building a display string by hand) -- fall back to the
        # absolute path rather than raising, since this is display-only.
        return str(path)

    @staticmethod
    def _under(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False
