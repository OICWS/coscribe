"""Converting between Office formats with LibreOffice.

Two jobs people ask for constantly and nothing else here does: open a legacy
file (.xls, .doc, .ppt -- the readers only take the OOXML formats) and
export a document, workbook or deck as a PDF.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..runtime.types import tool_metadata
from ._office_bins import find_soffice, run_soffice
from ._workspace import WorkspaceScope

# What each source type can become. Legacy and OpenDocument files can be
# brought up to the OOXML format; any Office file can be exported to PDF.
_TARGETS: dict[str, frozenset[str]] = {
    ".doc": frozenset({"docx", "pdf"}),
    ".rtf": frozenset({"docx", "pdf"}),
    ".odt": frozenset({"docx", "pdf"}),
    ".docx": frozenset({"pdf"}),
    ".xls": frozenset({"xlsx", "pdf"}),
    ".ods": frozenset({"xlsx", "pdf"}),
    ".xlsx": frozenset({"pdf"}),
    ".ppt": frozenset({"pptx", "pdf"}),
    ".odp": frozenset({"pptx", "pdf"}),
    ".pptx": frozenset({"pdf"}),
}

# A big deck or workbook takes a while; the first launch on a new profile
# takes longer still.
_CONVERT_TIMEOUT = 180.0

# One LibreOffice per profile at a time: a second instance on the same
# profile hands its job to the first and exits at once, with nothing made.
_PROFILE_LOCK = threading.Lock()


class ConvertToolkit:
    def __init__(
        self,
        root: str | Path,
        state_dir: str | Path | None = None,
        extra_readable: Sequence[str | Path] = (),
        extra_writable: Sequence[str | Path] = (),
    ) -> None:
        self._scope = WorkspaceScope(
            root, extra_readable=extra_readable, extra_writable=extra_writable
        )
        self._profile_dir = (
            Path(state_dir) / "lo_convert_profile"
            if state_dir is not None
            else Path(tempfile.gettempdir()) / "coscribe_lo_convert_profile"
        )

    def convert_office_file(
        self, path: str, to_format: str, output_path: str = ""
    ) -> dict[str, object]:
        source = self._scope.resolve(path)
        if not source.is_file():
            raise ValueError(f"File does not exist: {path}")
        target_format = to_format.strip().lower().lstrip(".")
        allowed = _TARGETS.get(source.suffix.lower())
        if allowed is None:
            raise ValueError(
                f"{source.suffix or 'This file'} isn't a type convert_office_file handles "
                f"({', '.join(sorted(_TARGETS))})."
            )
        if target_format not in allowed:
            raise ValueError(
                f"A {source.suffix} file can become: {', '.join(sorted(allowed))} "
                f"(not {to_format!r})."
            )
        destination = (
            self._scope.resolve(output_path, write=True)
            if output_path
            else self._scope.resolve(str(source.with_suffix(f".{target_format}")), write=True)
        )
        if destination.suffix.lower() != f".{target_format}":
            raise ValueError(f"output_path must end in .{target_format}")
        if destination.exists():
            raise FileExistsError(
                f"{self._scope.relative(destination)} already exists -- pick another "
                "output_path rather than replacing it."
            )
        soffice = find_soffice()
        if soffice is None:
            raise ValueError(
                "LibreOffice isn't installed (or couldn't be found), and converting needs it."
            )

        work = Path(tempfile.mkdtemp(prefix="coscribe_convert_"))
        try:
            with _PROFILE_LOCK:
                self._profile_dir.mkdir(parents=True, exist_ok=True)
                try:
                    run_soffice(
                        [
                            soffice,
                            "--headless",
                            "--norestore",
                            f"-env:UserInstallation={self._profile_dir.as_uri()}",
                            "--convert-to",
                            target_format,
                            "--outdir",
                            str(work),
                            str(source),
                        ],
                        _CONVERT_TIMEOUT,
                    )
                except subprocess.TimeoutExpired:
                    raise ValueError("LibreOffice took too long to convert this file.") from None
                except (subprocess.CalledProcessError, OSError) as error:
                    raise ValueError(f"LibreOffice could not convert this file: {error}") from None
            produced = work / f"{source.stem}.{target_format}"
            if not produced.is_file():
                raise ValueError(
                    "LibreOffice produced no file -- the source may be damaged or "
                    "password-protected."
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(produced), str(destination))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return {
            "path": self._scope.relative(destination),
            "bytes_written": destination.stat().st_size,
            "converted_from": self._scope.relative(source),
        }


def build_convert_tools(
    root: str | Path,
    *,
    state_dir: str | Path | None = None,
    extra_readable: Sequence[str | Path] = (),
    extra_writable: Sequence[str | Path] = (),
) -> list[Callable[..., Any]]:
    toolkit = ConvertToolkit(
        root, state_dir=state_dir, extra_readable=extra_readable, extra_writable=extra_writable
    )

    def convert_office_file(path: str, to_format: str, output_path: str = "") -> dict[str, object]:
        """Convert an Office file to another format with LibreOffice -- the
        way to open a legacy .xls/.doc/.ppt (the read tools take only
        .xlsx/.docx/.pptx: convert first, then read the new file) and the way
        to export a Word, Excel or PowerPoint file as a PDF.

        `.doc`/`.rtf`/`.odt` can become docx or pdf; `.xls`/`.ods` xlsx or
        pdf; `.ppt`/`.odp` pptx or pdf; `.docx`/`.xlsx`/`.pptx` pdf. The
        original is left as it is and an existing file is never replaced.

        Args:
            path: file to convert, relative to the workspace root
            to_format: "docx", "xlsx", "pptx" or "pdf"
            output_path: where to write the result; by default next to the
                original with the new extension
        """
        return toolkit.convert_office_file(path=path, to_format=to_format, output_path=output_path)

    return [
        tool_metadata(convert_office_file, risk_category="WRITE_LOCAL", category="documents"),
    ]
