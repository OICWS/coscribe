"""Tests for runtime_lg/exec_policy.py -- ROADMAP.md's Phase 7 item 2.
Pure unit tests against load_exec_policy/ExecPolicy directly; the real
wiring into web/session.py's _decide_action_request (forbidden/allow/
prompt precedence against plan mode) is covered end-to-end in
tests/test_web.py instead, since that's where a real turn/approval flow
already exists to exercise it through."""

from __future__ import annotations

import json
from pathlib import Path

from coscribe.runtime_lg.exec_policy import ExecPolicy, load_exec_policy


def _write_policy(path: Path, rules: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps({"rules": rules}), encoding="utf-8")
    return path


def test_no_path_configured_always_prompts() -> None:
    policy = load_exec_policy(None)
    assert policy.decide("import os") == ("prompt", None)


def test_missing_file_always_prompts(tmp_path: Path) -> None:
    policy = load_exec_policy(tmp_path / "does-not-exist.json")
    assert policy.decide("import os") == ("prompt", None)


def test_first_matching_rule_wins(tmp_path: Path) -> None:
    policy_path = _write_policy(
        tmp_path / "exec_policy.json",
        [
            {"pattern": r"subprocess", "decision": "prompt"},
            {"pattern": r"import", "decision": "allow"},
        ],
    )
    policy = load_exec_policy(policy_path)
    assert policy.decide("import subprocess") == ("prompt", None)
    assert policy.decide("import pandas") == ("allow", None)


def test_no_rule_matches_falls_back_to_prompt(tmp_path: Path) -> None:
    policy_path = _write_policy(
        tmp_path / "exec_policy.json", [{"pattern": r"nope", "decision": "allow"}]
    )
    policy = load_exec_policy(policy_path)
    assert policy.decide("print('hi')") == ("prompt", None)


def test_justification_is_returned_with_the_matching_decision(tmp_path: Path) -> None:
    policy_path = _write_policy(
        tmp_path / "exec_policy.json",
        [{"pattern": r"rm -rf", "decision": "forbidden", "justification": "no bulk deletes"}],
    )
    policy = load_exec_policy(policy_path)
    assert policy.decide("os.system('rm -rf /tmp/x')") == ("forbidden", "no bulk deletes")


def test_a_rule_with_no_justification_returns_none_not_an_empty_string(tmp_path: Path) -> None:
    policy_path = _write_policy(
        tmp_path / "exec_policy.json", [{"pattern": r"print", "decision": "allow"}]
    )
    policy = load_exec_policy(policy_path)
    assert policy.decide("print('hi')") == ("allow", None)


def test_malformed_json_falls_back_to_an_empty_policy_not_an_error(tmp_path: Path) -> None:
    policy_path = tmp_path / "exec_policy.json"
    policy_path.write_text("not valid json", encoding="utf-8")
    policy = load_exec_policy(policy_path)
    assert policy.decide("anything") == ("prompt", None)


def test_a_json_file_without_a_rules_array_falls_back_to_an_empty_policy(tmp_path: Path) -> None:
    policy_path = tmp_path / "exec_policy.json"
    policy_path.write_text(json.dumps({"not_rules": []}), encoding="utf-8")
    policy = load_exec_policy(policy_path)
    assert policy.decide("anything") == ("prompt", None)


def test_a_rule_with_an_invalid_regex_is_skipped_the_rest_still_load(tmp_path: Path) -> None:
    policy_path = _write_policy(
        tmp_path / "exec_policy.json",
        [
            {"pattern": "(unclosed", "decision": "allow"},
            {"pattern": r"print", "decision": "allow"},
        ],
    )
    policy = load_exec_policy(policy_path)
    assert policy.decide("print('hi')") == ("allow", None)


def test_a_rule_with_an_invalid_decision_is_skipped(tmp_path: Path) -> None:
    policy_path = _write_policy(
        tmp_path / "exec_policy.json", [{"pattern": r"print", "decision": "yolo-allow-everything"}]
    )
    policy = load_exec_policy(policy_path)
    assert policy.decide("print('hi')") == ("prompt", None)


def test_a_rule_missing_pattern_is_skipped(tmp_path: Path) -> None:
    policy_path = _write_policy(tmp_path / "exec_policy.json", [{"decision": "allow"}])
    policy = load_exec_policy(policy_path)
    assert policy.decide("anything") == ("prompt", None)


def test_a_non_object_rule_entry_is_skipped(tmp_path: Path) -> None:
    policy_path = tmp_path / "exec_policy.json"
    policy_path.write_text(json.dumps({"rules": ["not-an-object"]}), encoding="utf-8")
    policy = load_exec_policy(policy_path)
    assert policy.decide("anything") == ("prompt", None)


def test_pattern_matches_anywhere_in_the_script_not_just_at_the_start(tmp_path: Path) -> None:
    policy_path = _write_policy(
        tmp_path / "exec_policy.json", [{"pattern": r"shutil\.rmtree", "decision": "forbidden"}]
    )
    policy = load_exec_policy(policy_path)
    script = "import shutil\nfor d in dirs:\n    shutil.rmtree(d)\n"
    assert policy.decide(script) == ("forbidden", None)


def test_empty_exec_policy_always_prompts() -> None:
    assert ExecPolicy([]).decide("anything at all") == ("prompt", None)
