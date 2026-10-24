"""The Pythons on this computer, for the person to choose the scripts' interpreter from (#586).

PATH alone misses most installs on Windows: python.org's installer leaves "Add to PATH" off by
default, and the WindowsApps `python.exe` there only opens the Store. So the search also reads
the PEP 514 registry entries every python.org install writes, asks the `py` launcher, and looks
in the usual folders of per-user installs, Anaconda/Miniconda and uv. Each one found is run with
`--version`, so the list only offers interpreters that start.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

from .own_python import own_python_path

_VERSION = re.compile(r"Python (3\.\d+(?:\.\d+)?)")


def find_pythons(state_dir: Path) -> list[dict[str, str]]:
    """Each Python 3 that runs, as {"path", "version"}, coscribe's own first when downloaded."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in _candidates(state_dir):
        # One entry per interpreter: python and python3 are often links to the same file.
        key = os.path.normcase(os.path.realpath(candidate))
        if key in seen or "windowsapps" in os.path.normcase(candidate):
            continue
        seen.add(key)
        version = python_version(candidate)
        if version:
            found.append({"path": candidate, "version": version})
    return found


def python_version(path: str) -> str | None:
    """The version `path --version` reports, or None when it is not a Python 3 that runs."""
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _VERSION.search(result.stdout + result.stderr)
    return match.group(1) if result.returncode == 0 and match else None


def _candidates(state_dir: Path) -> Iterator[str]:
    own = own_python_path(state_dir)
    if own.is_file():
        yield str(own)
    if sys.platform == "win32":
        yield from _registered()
        yield from _launcher_list()
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            yield found
    if sys.platform == "win32":
        yield from _usual_folders()
    # Last: on a packaged build it is coscribe's own server, which `--version` leaves out.
    yield sys.executable


if sys.platform == "win32":
    import winreg

    def _registered() -> Iterator[str]:
        """PEP 514: HKCU and HKLM (both views) Software\\Python\\<company>\\<tag>\\InstallPath."""
        roots = [
            (winreg.HKEY_CURRENT_USER, 0),
            (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
            (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
        ]
        for root, view in roots:
            try:
                python_key = winreg.OpenKey(root, r"Software\Python", 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with python_key:
                for company in _subkeys(python_key):
                    if company == "PyLauncher":
                        continue
                    try:
                        company_key = winreg.OpenKey(python_key, company)
                    except OSError:
                        continue
                    with company_key:
                        for tag in _subkeys(company_key):
                            exe = _install_path(company_key, tag)
                            if exe:
                                yield exe

    def _subkeys(key: winreg.HKEYType) -> Iterator[str]:
        index = 0
        while True:
            try:
                yield winreg.EnumKey(key, index)
            except OSError:
                return
            index += 1

    def _install_path(company_key: winreg.HKEYType, tag: str) -> str | None:
        try:
            with winreg.OpenKey(company_key, rf"{tag}\InstallPath") as install:
                try:
                    exe, _ = winreg.QueryValueEx(install, "ExecutablePath")
                    if exe:
                        return str(exe)
                except OSError:
                    pass
                folder, _ = winreg.QueryValueEx(install, "")
        except OSError:
            return None
        exe_path = Path(str(folder)) / "python.exe"
        return str(exe_path) if exe_path.is_file() else None

else:

    def _registered() -> Iterator[str]:
        return iter(())


def _launcher_list() -> Iterator[str]:
    """`py -0p` lists what the launcher knows, one "-V:3.12 *  C:\\...\\python.exe" per line."""
    launcher = shutil.which("py")
    if not launcher:
        return
    try:
        result = subprocess.run(
            [launcher, "-0p"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    yield from launcher_paths(result.stdout)


def launcher_paths(listing: str) -> Iterator[str]:
    """The paths in `py -0p` output, whose lines read like "-V:3.12 *  C:\\...\\python.exe"."""
    for line in listing.splitlines():
        match = re.search(r"([A-Za-z]:\\.*python[w]?\.exe)\s*$", line.strip(), re.IGNORECASE)
        if match:
            yield match.group(1)


def _usual_folders() -> Iterator[str]:
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    program_data = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    patterns: Iterable[tuple[Path, str]] = [
        (local / "Programs" / "Python", "Python3*/python.exe"),
        (program_files, "Python3*/python.exe"),
        (Path("C:/"), "Python3*/python.exe"),
        (roaming / "uv" / "python", "cpython-3*/python.exe"),
    ]
    for base, pattern in patterns:
        if base.is_dir():
            for exe in sorted(base.glob(pattern), reverse=True):
                yield str(exe)
    # Windows paths ignore case, so one spelling of each finds either.
    for folder in ("anaconda3", "miniconda3"):
        for base in (home, program_data, local):
            exe = base / folder / "python.exe"
            if exe.is_file():
                yield str(exe)
