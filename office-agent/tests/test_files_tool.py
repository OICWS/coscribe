from pathlib import Path

import pytest

from coscribe.runtime.types import get_tool_metadata
from coscribe.tools.files import build_file_tools


def _tools_by_name(root: Path) -> dict[str, object]:
    return {tool.__name__: tool for tool in build_file_tools(root)}  # type: ignore[attr-defined]


def test_write_then_list_then_read(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="notes/todo.txt", content="buy milk")

    listed = tools["list_files"](path=".", pattern="*.txt", recursive=True)
    assert listed == {"files": ["notes/todo.txt"], "truncated": False, "total_count": 1}

    content = tools["read_file"](path="notes/todo.txt")
    assert content == "buy milk"


def test_write_file_on_a_new_file_reports_all_lines_added(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    result = tools["write_file"](path="a.txt", content="one\ntwo\nthree\n")

    assert result["lines_added"] == 3
    assert result["lines_removed"] == 0


def test_write_file_overwriting_reports_a_real_line_diff(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="one\ntwo\nthree\n")

    result = tools["write_file"](path="a.txt", content="one\nTWO\nthree\nfour\n")

    assert result["lines_added"] == 2
    assert result["lines_removed"] == 1


def test_read_file_head_returns_only_the_first_n_lines(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="one\ntwo\nthree\nfour\n")

    assert tools["read_file"](path="a.txt", head=2) == "one\ntwo\n"


def test_read_file_tail_returns_only_the_last_n_lines(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="one\ntwo\nthree\nfour\n")

    assert tools["read_file"](path="a.txt", tail=2) == "three\nfour\n"


def test_read_file_head_larger_than_file_returns_whole_file(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="one\ntwo\n")

    assert tools["read_file"](path="a.txt", head=100) == "one\ntwo\n"


def test_read_file_rejects_both_head_and_tail(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="one\ntwo\n")

    with pytest.raises(ValueError, match="cannot both be given"):
        tools["read_file"](path="a.txt", head=1, tail=1)


def test_get_file_info_reports_size_and_modified_time_for_a_file(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="hello")

    info = tools["get_file_info"](path="a.txt")

    assert info["path"] == "a.txt"
    assert info["is_directory"] is False
    assert info["size_bytes"] == 5
    # A real, parseable ISO 8601 timestamp -- not asserting an exact value
    # since the file was just created at "now."
    from datetime import datetime

    datetime.fromisoformat(str(info["modified"]))


def test_get_file_info_reports_a_directory_with_no_size(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    (tmp_path / "adir").mkdir()

    info = tools["get_file_info"](path="adir")

    assert info["path"] == "adir"
    assert info["is_directory"] is True
    assert info["size_bytes"] is None


def test_get_file_info_on_missing_path_raises(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    with pytest.raises(ValueError, match="does not exist"):
        tools["get_file_info"](path="missing.txt")


def test_list_files_truncates_past_200_but_reports_the_real_total(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    for i in range(210):
        tools["write_file"](path=f"file_{i:03d}.txt", content="x")

    result = tools["list_files"](path=".", pattern="*.txt")

    assert len(result["files"]) == 200
    assert result["truncated"] is True
    assert result["total_count"] == 210


def test_list_files_not_truncated_stays_false_with_accurate_count(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="x")
    tools["write_file"](path="b.txt", content="x")

    result = tools["list_files"](path=".", pattern="*.txt")

    assert result == {"files": ["a.txt", "b.txt"], "truncated": False, "total_count": 2}


def test_search_files_finds_substring_with_line_number(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="first\nsecond target line\nthird")

    result = tools["search_files"](query="target", path=".", pattern="*.txt")

    assert result == {
        "matches": [{"path": "a.txt", "line": 2, "text": "second target line"}],
        "truncated": False,
    }


def test_search_files_truncates_past_100_matches_and_says_so(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="\n".join(["target"] * 150))

    result = tools["search_files"](query="target", path=".", pattern="*.txt")

    assert len(result["matches"]) == 100
    assert result["truncated"] is True


def test_search_files_literal_query_does_not_treat_regex_metacharacters_specially(
    tmp_path: Path,
) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="a.b\naxb\n")

    result = tools["search_files"](query="a.b", path=".", pattern="*.txt")

    # "." is a literal dot here, not "any character" -- "axb" must not match.
    assert result["matches"] == [{"path": "a.txt", "line": 1, "text": "a.b"}]


def test_search_files_regex_finds_pattern_matches(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="port: 8080\nport: abc\nport: 22\n")

    result = tools["search_files"](query=r"port: \d+", path=".", pattern="*.txt", regex=True)

    assert result == {
        "matches": [
            {"path": "a.txt", "line": 1, "text": "port: 8080"},
            {"path": "a.txt", "line": 3, "text": "port: 22"},
        ],
        "truncated": False,
    }


def test_search_files_regex_supports_alternation_and_inline_case_insensitivity(
    tmp_path: Path,
) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="TODO: fix this\nFIXME later\nnothing here\n")

    result = tools["search_files"](
        query=r"(?i)todo|fixme", path=".", pattern="*.txt", regex=True
    )

    assert [m["line"] for m in result["matches"]] == [1, 2]


def test_search_files_invalid_regex_raises_clear_error(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="hello")

    with pytest.raises(ValueError, match="Invalid regex"):
        tools["search_files"](query="(unclosed", path=".", pattern="*.txt", regex=True)


def test_write_file_does_not_overwrite_when_disabled(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="original")

    with pytest.raises(FileExistsError):
        tools["write_file"](path="a.txt", content="replacement", overwrite=False)

    assert tools["read_file"](path="a.txt") == "original"


def test_edit_file_replaces_a_unique_substring(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="hello world\nsecond line\n")

    result = tools["edit_file"](path="a.txt", old_text="world", new_text="there")

    assert result == {"path": "a.txt", "replacements": 1, "lines_added": 1, "lines_removed": 1}
    assert tools["read_file"](path="a.txt") == "hello there\nsecond line\n"


def test_edit_file_only_touches_the_matched_text(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="line one\nline two\nline three\n")

    tools["edit_file"](path="a.txt", old_text="line two", new_text="LINE TWO")

    assert tools["read_file"](path="a.txt") == "line one\nLINE TWO\nline three\n"


def test_edit_file_rejects_ambiguous_match_without_replace_all(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="foo\nfoo\nfoo\n")

    with pytest.raises(ValueError, match="appears 3 times"):
        tools["edit_file"](path="a.txt", old_text="foo", new_text="bar")

    assert tools["read_file"](path="a.txt") == "foo\nfoo\nfoo\n"


def test_edit_file_replace_all_replaces_every_occurrence(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="foo\nfoo\nfoo\n")

    result = tools["edit_file"](
        path="a.txt", old_text="foo", new_text="bar", replace_all=True
    )

    assert result == {"path": "a.txt", "replacements": 3, "lines_added": 3, "lines_removed": 3}
    assert tools["read_file"](path="a.txt") == "bar\nbar\nbar\n"


def test_edit_file_rejects_text_not_found(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="hello world\n")

    with pytest.raises(ValueError, match="not found"):
        tools["edit_file"](path="a.txt", old_text="goodbye", new_text="hi")


def test_edit_file_rejects_empty_old_text(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="hello world\n")

    with pytest.raises(ValueError, match="cannot be empty"):
        tools["edit_file"](path="a.txt", old_text="", new_text="hi")


def test_edit_file_rejects_identical_old_and_new_text(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="hello world\n")

    with pytest.raises(ValueError, match="identical"):
        tools["edit_file"](path="a.txt", old_text="world", new_text="world")


def test_edit_file_on_missing_file_raises(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    with pytest.raises(ValueError, match="does not exist"):
        tools["edit_file"](path="missing.txt", old_text="a", new_text="b")


def test_edit_file_rejects_a_directory(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    (tmp_path / "adir").mkdir()

    with pytest.raises(ValueError, match="not a file"):
        tools["edit_file"](path="adir", old_text="a", new_text="b")


def test_edit_file_batch_applies_all_edits_in_order(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="hello world\nsecond line\n")

    result = tools["edit_file_batch"](
        path="a.txt",
        edits="world\n---\nthere\n---\nsecond line\n---\nSECOND LINE",
    )

    assert result == {
        "path": "a.txt",
        "edits_applied": 2,
        "lines_added": 2,
        "lines_removed": 2,
    }
    assert tools["read_file"](path="a.txt") == "hello there\nSECOND LINE\n"


def test_edit_file_batch_later_edit_can_match_text_an_earlier_edit_introduced(
    tmp_path: Path,
) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="foo\n")

    result = tools["edit_file_batch"](path="a.txt", edits="foo\n---\nbar\n---\nbar\n---\nbaz")

    assert result == {
        "path": "a.txt",
        "edits_applied": 2,
        "lines_added": 1,
        "lines_removed": 1,
    }
    assert tools["read_file"](path="a.txt") == "baz\n"


def test_edit_file_batch_is_atomic_a_bad_edit_leaves_the_file_untouched(
    tmp_path: Path,
) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="hello world\n")

    with pytest.raises(ValueError, match="not found"):
        tools["edit_file_batch"](
            path="a.txt",
            edits="world\n---\nthere\n---\ndoes-not-exist\n---\nx",
        )

    assert tools["read_file"](path="a.txt") == "hello world\n"


def test_edit_file_batch_rejects_an_ambiguous_edit_with_no_per_edit_replace_all(
    tmp_path: Path,
) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="foo\nfoo\n")

    with pytest.raises(ValueError, match="appears 2 times"):
        tools["edit_file_batch"](path="a.txt", edits="foo\n---\nbar")

    assert tools["read_file"](path="a.txt") == "foo\nfoo\n"


def test_edit_file_batch_rejects_an_empty_edits_string(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="hello world\n")

    with pytest.raises(ValueError, match="at least one"):
        tools["edit_file_batch"](path="a.txt", edits="")


def test_edit_file_batch_rejects_an_odd_number_of_chunks(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="hello world\n")

    with pytest.raises(ValueError, match="odd number"):
        tools["edit_file_batch"](path="a.txt", edits="world\n---\nthere\n---\nsecond line")


def test_edit_file_batch_rejects_identical_old_and_new_text(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="hello world\n")

    with pytest.raises(ValueError, match="identical"):
        tools["edit_file_batch"](path="a.txt", edits="world\n---\nworld")


def test_edit_file_batch_on_missing_file_raises(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    with pytest.raises(ValueError, match="does not exist"):
        tools["edit_file_batch"](path="missing.txt", edits="a\n---\nb")


def test_path_cannot_escape_workspace_root(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    with pytest.raises(PermissionError):
        tools["read_file"](path="../outside.txt")


def test_delete_file_removes_it(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="gone soon")

    result = tools["delete_file"](path="a.txt")

    assert result == {"path": "a.txt", "deleted": True}
    assert not (tmp_path / "a.txt").exists()


def test_delete_file_on_missing_file_raises(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    with pytest.raises(ValueError, match="does not exist"):
        tools["delete_file"](path="missing.txt")


def test_delete_file_rejects_a_directory(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    (tmp_path / "adir").mkdir()

    with pytest.raises(ValueError, match="run_python_script"):
        tools["delete_file"](path="adir")


def test_move_file_renames_within_the_same_directory(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="old.txt", content="hi")

    result = tools["move_file"](source="old.txt", destination="new.txt")

    assert result == {"source": "old.txt", "destination": "new.txt"}
    assert not (tmp_path / "old.txt").exists()
    assert (tmp_path / "new.txt").read_text() == "hi"


def test_move_file_relocates_across_directories_creating_parents(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="hi")

    tools["move_file"](source="a.txt", destination="archive/2025/a.txt")

    assert not (tmp_path / "a.txt").exists()
    assert (tmp_path / "archive" / "2025" / "a.txt").read_text() == "hi"


def test_move_file_moves_a_whole_directory(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="olddir/a.txt", content="hi")

    tools["move_file"](source="olddir", destination="newdir")

    assert not (tmp_path / "olddir").exists()
    assert (tmp_path / "newdir" / "a.txt").read_text() == "hi"


def test_move_file_without_overwrite_rejects_existing_destination(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="1")
    tools["write_file"](path="b.txt", content="2")

    with pytest.raises(FileExistsError):
        tools["move_file"](source="a.txt", destination="b.txt")


def test_move_file_with_overwrite_replaces_existing_file(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="new")
    tools["write_file"](path="b.txt", content="old")

    tools["move_file"](source="a.txt", destination="b.txt", overwrite=True)

    assert (tmp_path / "b.txt").read_text() == "new"


def test_move_file_rejects_an_existing_directory_destination_even_with_overwrite(
    tmp_path: Path,
) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="hi")
    (tmp_path / "adir").mkdir()

    with pytest.raises(ValueError, match="run_python_script"):
        tools["move_file"](source="a.txt", destination="adir", overwrite=True)


def test_move_file_on_missing_source_raises(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    with pytest.raises(ValueError, match="does not exist"):
        tools["move_file"](source="missing.txt", destination="dest.txt")


def test_copy_file_leaves_source_in_place(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="hi")

    result = tools["copy_file"](source="a.txt", destination="b.txt")

    assert result == {"source": "a.txt", "destination": "b.txt"}
    assert (tmp_path / "a.txt").read_text() == "hi"
    assert (tmp_path / "b.txt").read_text() == "hi"


def test_copy_file_preserves_binary_content_byte_for_byte(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    binary_bytes = bytes(range(256))
    (tmp_path / "image.bin").write_bytes(binary_bytes)

    tools["copy_file"](source="image.bin", destination="copy.bin")

    assert (tmp_path / "copy.bin").read_bytes() == binary_bytes


def test_copy_file_without_overwrite_rejects_existing_destination(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_file"](path="a.txt", content="1")
    tools["write_file"](path="b.txt", content="2")

    with pytest.raises(FileExistsError):
        tools["copy_file"](source="a.txt", destination="b.txt")


def test_copy_file_rejects_a_directory_source(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    (tmp_path / "adir").mkdir()

    with pytest.raises(ValueError, match="run_python_script"):
        tools["copy_file"](source="adir", destination="bdir")


def test_read_only_tools_are_low_risk_write_requires_approval(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    for name in ("list_files", "read_file", "search_files", "get_file_info"):
        metadata = get_tool_metadata(tools[name])
        assert metadata.risk_category == "READ"
        assert metadata.requires_approval is False

    for name in ("write_file", "edit_file", "delete_file", "move_file", "copy_file"):
        write_metadata = get_tool_metadata(tools[name])
        assert write_metadata.risk_category == "WRITE_LOCAL"
        assert write_metadata.requires_approval is True


def test_extra_readable_dir_allows_read_but_not_write(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "report.txt").write_text("quarterly numbers")
    workspace = tmp_path / "workspace"

    tools = {
        tool.__name__: tool
        for tool in build_file_tools(workspace, extra_readable=[downloads])  # type: ignore[attr-defined]
    }

    assert tools["read_file"](path=str(downloads / "report.txt")) == "quarterly numbers"
    assert tools["list_files"](path=str(downloads))["files"] == ["report.txt"]

    with pytest.raises(PermissionError):
        tools["write_file"](path=str(downloads / "report.txt"), content="tampered")


def test_extra_writable_dir_allows_read_and_write(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    workspace = tmp_path / "workspace"

    tools = {
        tool.__name__: tool
        for tool in build_file_tools(workspace, extra_writable=[shared])  # type: ignore[attr-defined]
    }

    tools["write_file"](path=str(shared / "out.txt"), content="hello")
    assert tools["read_file"](path=str(shared / "out.txt")) == "hello"


def test_copy_file_can_read_from_extra_readable_dir_into_the_workspace(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "report.txt").write_text("quarterly numbers")
    workspace = tmp_path / "workspace"

    tools = {
        tool.__name__: tool
        for tool in build_file_tools(workspace, extra_readable=[downloads])  # type: ignore[attr-defined]
    }

    tools["copy_file"](source=str(downloads / "report.txt"), destination="report.txt")

    assert (workspace / "report.txt").read_text() == "quarterly numbers"


def test_copy_file_into_a_read_only_extra_dir_is_rejected(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    workspace = tmp_path / "workspace"

    tools = {
        tool.__name__: tool
        for tool in build_file_tools(workspace, extra_readable=[downloads])  # type: ignore[attr-defined]
    }
    tools["write_file"](path="a.txt", content="hi")

    with pytest.raises(PermissionError):
        tools["copy_file"](source="a.txt", destination=str(downloads / "a.txt"))


def test_path_outside_extra_dirs_still_rejected(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "secret.txt").write_text("nope")
    workspace = tmp_path / "workspace"

    tools = {
        tool.__name__: tool
        for tool in build_file_tools(workspace, extra_readable=[downloads])  # type: ignore[attr-defined]
    }

    with pytest.raises(PermissionError):
        tools["read_file"](path=str(elsewhere / "secret.txt"))
