import shutil
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from coscribe.tools.node_env import (
    ensure_node_env,
    install_package,
    list_packages,
    uninstall_package,
)


def _node_and_npm_available() -> bool:
    return shutil.which("node") is not None and shutil.which("npm") is not None


def _network_reachable() -> bool:
    """ensure_node_env's first call needs the npm registry to seed
    pptxgenjs -- skip cleanly offline, same reasoning as
    test_script_env.py's identical helper."""
    try:
        socket.create_connection(("registry.npmjs.org", 443), timeout=5).close()
        return True
    except OSError:
        return False


pytestmark_network = pytest.mark.skipif(
    not _node_and_npm_available() or not _network_reachable(),
    reason="node/npm not installed, or registry.npmjs.org not reachable from this environment",
)


@pytest.fixture(scope="module")
def real_state_dir() -> Iterator[Path]:
    """Module-scoped so the underlying node-env's one-time baseline-package
    install (a real network-bound cost) happens once for every test in
    this file, not once per test."""
    state_dir = Path(tempfile.mkdtemp(prefix="coscribe_node_env_test_"))
    yield state_dir
    shutil.rmtree(state_dir, ignore_errors=True)


def test_ensure_node_env_raises_a_clear_error_when_node_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("coscribe.tools.node_env.shutil.which", lambda name: None)

    with pytest.raises(RuntimeError, match="node.*npm.*not found"):
        ensure_node_env(tmp_path)


@pytestmark_network
def test_ensure_node_env_creates_a_working_node_env(real_state_dir: Path) -> None:
    node_env_dir = ensure_node_env(real_state_dir)

    assert (node_env_dir / "node_modules" / "pptxgenjs").is_dir()


@pytestmark_network
def test_ensure_node_env_is_idempotent(real_state_dir: Path) -> None:
    first = ensure_node_env(real_state_dir)
    second = ensure_node_env(real_state_dir)

    assert first == second


@pytestmark_network
def test_ensure_node_env_seeds_pptxgenjs(real_state_dir: Path) -> None:
    ensure_node_env(real_state_dir)

    names = {pkg["name"].lower() for pkg in list_packages(real_state_dir)}
    assert "pptxgenjs" in names


@pytestmark_network
def test_install_and_uninstall_a_package_round_trips(real_state_dir: Path) -> None:
    ensure_node_env(real_state_dir)

    result = install_package(real_state_dir, "left-pad")
    assert result == {"success": True, "error": None}
    names_after_install = {pkg["name"].lower() for pkg in list_packages(real_state_dir)}
    assert "left-pad" in names_after_install

    result2 = uninstall_package(real_state_dir, "left-pad")
    assert result2 == {"success": True, "error": None}
    names_after_uninstall = {pkg["name"].lower() for pkg in list_packages(real_state_dir)}
    assert "left-pad" not in names_after_uninstall


@pytestmark_network
def test_install_unknown_package_returns_a_specific_error_not_an_exception(
    real_state_dir: Path,
) -> None:
    ensure_node_env(real_state_dir)

    result = install_package(real_state_dir, "not-a-real-package-xyz123")

    assert result["success"] is False
    assert "not-a-real-package-xyz123" in str(result["error"])


@pytestmark_network
def test_install_package_times_out_gracefully(real_state_dir: Path) -> None:
    ensure_node_env(real_state_dir)

    result = install_package(real_state_dir, "left-pad", timeout=0.001)

    assert result["success"] is False
    assert "timed out" in str(result["error"])
