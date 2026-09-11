from pathlib import Path

import pytest

from coscribe.web.browser_detect import find_windows_browser


def test_returns_edge_path_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_text("")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "nonexistent"))
    monkeypatch.setenv("LocalAppData", str(tmp_path / "nonexistent"))

    assert find_windows_browser() == str(edge)


def test_returns_none_when_nothing_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "nonexistent"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "nonexistent"))
    monkeypatch.setenv("LocalAppData", str(tmp_path / "nonexistent"))

    assert find_windows_browser() is None


def test_prefers_edge_over_chrome_when_both_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_text("")
    chrome = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "nonexistent"))
    monkeypatch.setenv("LocalAppData", str(tmp_path / "nonexistent"))

    assert find_windows_browser() == str(edge)
