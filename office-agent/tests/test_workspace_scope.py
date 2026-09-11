from pathlib import Path

import pytest

from coscribe.tools._workspace import WorkspaceScope


def test_resolve_confines_relative_paths_to_root(tmp_path: Path) -> None:
    scope = WorkspaceScope(tmp_path / "ws")

    resolved = scope.resolve("notes/todo.txt")

    assert resolved == (tmp_path / "ws" / "notes" / "todo.txt").resolve()


def test_resolve_rejects_path_traversal_out_of_root(tmp_path: Path) -> None:
    scope = WorkspaceScope(tmp_path / "ws")

    with pytest.raises(PermissionError):
        scope.resolve("../outside.txt")


def test_resolve_rejects_absolute_path_with_no_extra_dirs(tmp_path: Path) -> None:
    scope = WorkspaceScope(tmp_path / "ws")
    outside = tmp_path / "outside.txt"

    with pytest.raises(PermissionError):
        scope.resolve(str(outside))


def test_extra_readable_allows_read_not_write(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    scope = WorkspaceScope(tmp_path / "ws", extra_readable=[extra])
    target = extra / "file.txt"

    assert scope.resolve(str(target)) == target.resolve()
    with pytest.raises(PermissionError):
        scope.resolve(str(target), write=True)


def test_extra_writable_allows_both(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    scope = WorkspaceScope(tmp_path / "ws", extra_writable=[extra])
    target = extra / "file.txt"

    assert scope.resolve(str(target)) == target.resolve()
    assert scope.resolve(str(target), write=True) == target.resolve()


def test_root_itself_is_always_writable_regardless_of_extra_dirs(tmp_path: Path) -> None:
    scope = WorkspaceScope(tmp_path / "ws", extra_readable=[tmp_path / "extra"])

    resolved = scope.resolve("note.txt", write=True)

    assert resolved == (tmp_path / "ws" / "note.txt").resolve()


def test_relative_reports_a_path_relative_to_whichever_root_it_is_under(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    scope = WorkspaceScope(tmp_path / "ws", extra_readable=[extra])

    assert scope.relative((tmp_path / "ws" / "a.txt").resolve()) == "a.txt"
    assert scope.relative((extra / "b.txt").resolve()) == "b.txt"


def test_unrelated_directory_is_rejected_even_with_extra_dirs_configured(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    elsewhere = tmp_path / "elsewhere"
    scope = WorkspaceScope(tmp_path / "ws", extra_readable=[extra])

    with pytest.raises(PermissionError):
        scope.resolve(str(elsewhere / "file.txt"))
