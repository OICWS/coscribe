"""Tests for runtime_lg/audit.py -- ROADMAP.md's Phase 4 "Audit logging"
item. Pure unit tests against AuditLog/record_decision directly; the real
wiring into web/session.py's _decide_action_request (hook veto, plan
mode, accept-edits, human approve/deny) is covered end-to-end in
tests/test_web.py instead, since that's where a real turn/approval flow
already exists to exercise it through."""

from __future__ import annotations

from pathlib import Path

from coscribe.runtime_lg.audit import AuditEntry, AuditLog, record_decision, redact_secrets


def test_read_all_on_a_missing_file_returns_empty_not_an_error(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    assert log.read_all() == []


def test_append_then_read_all_round_trips(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    record_decision(
        log,
        thread_id="t1",
        tool_name="write_file",
        arguments={"path": "a.txt", "content": "hi"},
        decision="approve",
        reason="human",
    )
    record_decision(
        log,
        thread_id="t1",
        tool_name="delete_file",
        arguments={"path": "b.txt"},
        decision="reject",
        reason="plan_mode",
        detail="Plan mode is active (read-only).",
    )

    entries = log.read_all()
    assert len(entries) == 2
    assert entries[0].tool_name == "write_file"
    assert entries[0].decision == "approve"
    assert entries[0].reason == "human"
    assert entries[0].detail is None
    assert entries[0].arguments == {"path": "a.txt", "content": "hi"}
    assert entries[1].tool_name == "delete_file"
    assert entries[1].decision == "reject"
    assert entries[1].reason == "plan_mode"
    assert entries[1].detail == "Plan mode is active (read-only)."
    # A real, parseable ISO-8601 timestamp -- not asserting the exact
    # value, just that record_decision actually filled it in.
    assert entries[0].timestamp


def test_append_is_additive_across_separate_audit_log_instances(tmp_path: Path) -> None:
    """The log is a plain file on disk, not held open/cached in memory --
    a fresh AuditLog(state_dir) pointed at the same directory (e.g. a new
    turn, a different ChatSessionLG instance) must see and add to the
    same history, not start a new file or clobber the old one."""
    record_decision(
        AuditLog(tmp_path),
        thread_id="t1",
        tool_name="write_file",
        arguments={},
        decision="approve",
        reason="human",
    )
    record_decision(
        AuditLog(tmp_path),
        thread_id="t2",
        tool_name="run_python_script",
        arguments={},
        decision="reject",
        reason="hook_veto",
        detail="blocked by policy",
    )

    entries = AuditLog(tmp_path).read_all()
    assert [e.thread_id for e in entries] == ["t1", "t2"]


def test_read_all_skips_an_unparseable_line_instead_of_raising(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    record_decision(
        log,
        thread_id="t1",
        tool_name="write_file",
        arguments={},
        decision="approve",
        reason="human",
    )
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write("not valid json\n")
        handle.write("\n")  # a bare blank line must also be skipped, not error
    record_decision(
        log,
        thread_id="t1",
        tool_name="delete_file",
        arguments={},
        decision="approve",
        reason="accept_edits",
    )

    entries = log.read_all()
    assert [e.tool_name for e in entries] == ["write_file", "delete_file"]


def test_redact_secrets_blanks_a_value_whose_key_name_looks_secret_shaped() -> None:
    result = redact_secrets(
        {"api_key": "anything-at-all", "password": "hunter2", "note": "fine"}
    )
    assert result == {"api_key": "[REDACTED]", "password": "[REDACTED]", "note": "fine"}


def test_redact_secrets_matches_known_provider_token_formats_regardless_of_key_name() -> None:
    result = redact_secrets(
        {"code": "client = Anthropic(api_key='sk-ant-abcdefghijklmnopqrstuvwx')"}
    )
    assert "sk-ant-" not in result["code"]
    assert "[REDACTED]" in result["code"]


def test_redact_secrets_recurses_into_nested_dicts_and_lists() -> None:
    result = redact_secrets(
        {"env": {"GITHUB_TOKEN": "ghp_" + "a" * 36}, "args": ["--flag", "AKIAABCDEFGHIJKLMNOP"]}
    )
    assert result["env"]["GITHUB_TOKEN"] == "[REDACTED]"
    assert result["args"] == ["--flag", "[REDACTED]"]


def test_redact_secrets_leaves_ordinary_long_strings_and_non_strings_alone() -> None:
    long_benign_text = "This is a perfectly ordinary long paragraph of file content. " * 3
    payload = {"content": long_benign_text, "count": 5, "enabled": True, "missing": None}
    assert redact_secrets(payload) == payload


def test_record_decision_redacts_secrets_in_arguments_and_detail(tmp_path: Path) -> None:
    log = AuditLog(tmp_path)
    record_decision(
        log,
        thread_id="t1",
        tool_name="add_mcp_server",
        arguments={"name": "gh", "env": {"GITHUB_TOKEN": "ghp_" + "b" * 36}},
        decision="approve",
        reason="human",
        detail="approved with api_key=sk-ant-zzzzzzzzzzzzzzzzzzzzzz present",
    )

    entries = log.read_all()
    assert entries[0].arguments == {"name": "gh", "env": {"GITHUB_TOKEN": "[REDACTED]"}}
    assert "sk-ant-" not in (entries[0].detail or "")
    assert "[REDACTED]" in (entries[0].detail or "")


def test_audit_entry_is_a_plain_dataclass_round_trippable_through_json() -> None:
    entry = AuditEntry(
        timestamp="2026-01-01T00:00:00+00:00",
        thread_id="t1",
        tool_name="write_file",
        arguments={"path": "a.txt"},
        decision="approve",
        reason="human",
    )
    assert entry.detail is None
