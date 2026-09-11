import shutil
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from coscribe.tools.script_env import (
    ensure_script_env,
    install_package,
    list_packages,
    uninstall_package,
    venv_python,
)


def _network_reachable() -> bool:
    """ensure_script_env needs pypi.org to seed baseline packages -- skip
    the real-venv tests cleanly (like the LibreOffice-gated tests
    elsewhere) rather than failing in an offline environment."""
    try:
        socket.create_connection(("pypi.org", 443), timeout=5).close()
        return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def real_state_dir() -> Iterator[Path]:
    """Module-scoped so the real venv (and its baseline-package install,
    a real ~20s network-bound cost) happens once for every test in this
    file, not once per test."""
    state_dir = Path(tempfile.mkdtemp(prefix="coscribe_script_env_test_"))
    yield state_dir
    shutil.rmtree(state_dir, ignore_errors=True)


pytestmark_network = pytest.mark.skipif(
    not _network_reachable(), reason="pypi.org not reachable from this environment"
)


def test_venv_python_uses_scripts_python_exe_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for a real, live-reported bug: venv creates
    Scripts\\python.exe on Windows, not bin/python -- run_python_script and
    the Environment tab's package listing both raised FileNotFoundError on
    a real Windows machine before this platform branch existed."""
    monkeypatch.setattr("coscribe.tools.script_env.sys.platform", "win32")

    result = venv_python(Path("C:/state/script-env"))

    assert result == Path("C:/state/script-env/Scripts/python.exe")


def test_venv_python_uses_bin_python_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("coscribe.tools.script_env.sys.platform", "linux")

    result = venv_python(Path("/state/script-env"))

    assert result == Path("/state/script-env/bin/python")


@pytestmark_network
def test_ensure_script_env_creates_a_working_venv(real_state_dir: Path) -> None:
    venv_dir = ensure_script_env(real_state_dir)

    assert venv_python(venv_dir).is_file()


@pytestmark_network
def test_ensure_script_env_falls_back_to_a_real_python_when_sys_executable_cant_create_a_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: a packaged desktop
    build's sys.executable is the frozen coscribe-server.exe itself, not
    a real python.exe -- `subprocess.run([sys.executable, "-m", "venv",
    path])` never reaches venv's module runner at all, it's rejected by
    this app's own --host/--port argparse as "unrecognized arguments"
    instead, on a fresh Windows machine that had never used this feature
    before ("unrecognized arguments: -m venv <script-env path>", verbatim
    from the report). Simulated here by pointing sys.executable at a
    binary that exits nonzero for any arguments (stands in for the
    frozen exe's own argparse rejecting "-m venv <path>") -- proves
    _venv_create_candidates' fallback chain (py/python3/python found via
    PATH) still produces a real, working venv instead of surfacing that
    failure. Uses a fresh tmp_path, not the module-scoped real_state_dir
    fixture -- ensure_script_env is idempotent (returns early if the venv
    already exists), so reusing that shared fixture here would skip the
    fallback path entirely rather than exercising it."""
    monkeypatch.setattr("coscribe.tools.script_env.sys.executable", "/bin/false")

    venv_dir = ensure_script_env(tmp_path)

    assert venv_python(venv_dir).is_file()


@pytestmark_network
def test_ensure_script_env_is_idempotent(real_state_dir: Path) -> None:
    first = ensure_script_env(real_state_dir)
    second = ensure_script_env(real_state_dir)

    assert first == second


@pytestmark_network
def test_ensure_script_env_seeds_baseline_packages(real_state_dir: Path) -> None:
    ensure_script_env(real_state_dir)

    names = {pkg["name"].lower() for pkg in list_packages(real_state_dir)}
    for expected in ("openpyxl", "python-docx", "python-pptx", "pandas", "pdfplumber"):
        assert expected in names


@pytestmark_network
def test_list_packages_excludes_venv_scaffolding(real_state_dir: Path) -> None:
    ensure_script_env(real_state_dir)

    names = {pkg["name"].lower() for pkg in list_packages(real_state_dir)}
    assert "pip" not in names
    assert "setuptools" not in names


@pytestmark_network
def test_install_and_uninstall_a_package_round_trips(real_state_dir: Path) -> None:
    ensure_script_env(real_state_dir)

    result = install_package(real_state_dir, "six")
    assert result == {"success": True, "error": None}
    names_after_install = {pkg["name"].lower() for pkg in list_packages(real_state_dir)}
    assert "six" in names_after_install

    result2 = uninstall_package(real_state_dir, "six")
    assert result2 == {"success": True, "error": None}
    names_after_uninstall = {pkg["name"].lower() for pkg in list_packages(real_state_dir)}
    assert "six" not in names_after_uninstall


@pytestmark_network
def test_install_unknown_package_returns_a_specific_error_not_an_exception(
    real_state_dir: Path,
) -> None:
    ensure_script_env(real_state_dir)

    result = install_package(real_state_dir, "not-a-real-package-xyz123")

    assert result["success"] is False
    assert "not-a-real-package-xyz123" in str(result["error"])


@pytestmark_network
def test_install_package_times_out_gracefully(real_state_dir: Path) -> None:
    ensure_script_env(real_state_dir)

    result = install_package(real_state_dir, "six", timeout=0.001)

    assert result["success"] is False
    assert "timed out" in str(result["error"])
