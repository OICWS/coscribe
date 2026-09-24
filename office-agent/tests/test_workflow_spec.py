import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from coscribe.workflows.refs import (
    UnresolvedReference,
    evaluate,
    render_text,
    render_value,
    resolve,
)
from coscribe.workflows.spec import Condition, parse_workflow

FIXTURE = Path(__file__).parent / "fixtures" / "pdf_error_audit_workflow.json"


def _audit() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_pdf_audit_workflow_parses() -> None:
    workflow = parse_workflow(_audit())

    assert [s.kind for s in workflow.steps] == [
        "tool", "tool", "check", "script", "llm", "check", "approval", "tool",
    ]  # fmt: skip
    assert workflow.inputs[0].default == "big_manual.pdf"


def test_a_reference_to_a_later_step_is_rejected_at_save_time() -> None:
    data = _audit()
    data["steps"][0], data["steps"][4] = data["steps"][4], data["steps"][0]

    with pytest.raises(ValidationError, match=r"reads 'page_count.pages', which isn't an input"):
        parse_workflow(data)


def test_a_typo_in_a_reference_is_rejected() -> None:
    data = _audit()
    data["steps"][5]["conditions"][0]["left"] = {"ref": "sumary.error_count"}

    with pytest.raises(ValidationError, match="sumary.error_count"):
        parse_workflow(data)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["steps"][1].update(id="search"), "id 'search' is used twice"),
        (lambda d: d["steps"][1].update(save_as="matches"), "'matches' is already taken"),
        (lambda d: d["steps"][1].update(save_as="pdf_path"), "'pdf_path' is already taken"),
        (lambda d: d["steps"][0].update(id="Search Step"), "must start with a lowercase letter"),
        (lambda d: d["steps"][4].update(fields=[]), "at least 1 item"),
        (lambda d: d["steps"][4].pop("save_as"), "save_as"),
        (lambda d: d["steps"][0].update(kind="agent"), "tag 'agent'"),
        (lambda d: d["steps"][0].update(surprise=1), "surprise"),
    ],
)
def test_malformed_workflows_are_rejected(mutate: Any, message: str) -> None:
    data = _audit()
    mutate(data)

    with pytest.raises(ValidationError, match=message):
        parse_workflow(data)


def test_an_operand_is_exactly_one_thing() -> None:
    with pytest.raises(ValidationError, match="exactly one of"):
        Condition.model_validate(
            {"left": {"ref": "a", "value": 1}, "op": "eq", "right": {"value": 1}}
        )
    with pytest.raises(ValidationError, match="not_empty takes no right-hand side"):
        Condition.model_validate({"left": {"ref": "a"}, "op": "not_empty", "right": {"value": 1}})
    with pytest.raises(ValidationError, match="needs one"):
        Condition.model_validate({"left": {"ref": "a"}, "op": "eq"})


VALUES = {
    "pdf_path": "big.pdf",
    "matches": [{"page": 97, "text": "Error code E-0097"}, {"page": 194, "text": "x"}],
    "summary": {"error_count": 2, "accounts": ["4410-97"], "boilerplate": True},
}


def test_resolve_walks_dicts_and_list_indexes() -> None:
    assert resolve("matches.1.page", VALUES) == 194
    assert resolve("summary.accounts", VALUES) == ["4410-97"]
    with pytest.raises(UnresolvedReference, match="'summary' has no 'total'"):
        resolve("summary.total", VALUES)
    with pytest.raises(UnresolvedReference, match="'later' has no value yet"):
        resolve("later", VALUES)


def test_a_whole_reference_keeps_its_type_and_embedded_ones_become_text() -> None:
    assert render_value("{{summary.accounts}}", VALUES) == ["4410-97"]
    assert render_value({"path": "{{pdf_path}}", "n": 3}, VALUES) == {"path": "big.pdf", "n": 3}
    assert (
        render_text("File {{pdf_path}}, {{summary.error_count}} errors", VALUES)
        == "File big.pdf, 2 errors"
    )
    assert '"page": 97' in render_text("{{matches}}", VALUES)
    assert render_text("Accounts: {{summary.accounts}}", {"summary": {"accounts": ["a", "b"]}}) == (
        "Accounts: a, b"
    )


def _condition(left: dict[str, Any], op: str, right: dict[str, Any] | None = None) -> Condition:
    return Condition.model_validate(
        {"left": left, "op": op, "right": right} if right else {"left": left, "op": op}
    )


def test_evaluate_reports_what_it_compared() -> None:
    assert evaluate(
        _condition({"count": "matches"}, "eq", {"ref": "summary.error_count"}), VALUES
    ) == (True, 2, 2)
    assert evaluate(
        _condition({"count": "summary.accounts"}, "eq", {"ref": "summary.error_count"}), VALUES
    ) == (
        False,
        1,
        2,
    )
    assert (
        evaluate(_condition({"ref": "summary.error_count"}, "eq", {"value": "2"}), VALUES)[0]
        is True
    )
    assert (
        evaluate(_condition({"ref": "summary.error_count"}, "gt", {"value": 1}), VALUES)[0] is True
    )
    assert (
        evaluate(_condition({"ref": "summary.accounts"}, "contains", {"value": "4410-97"}), VALUES)[
            0
        ]
        is True
    )
    assert (
        evaluate(_condition({"ref": "summary.boilerplate"}, "eq", {"value": False}), VALUES)[0]
        is False
    )
    assert evaluate(_condition({"ref": "summary.accounts"}, "not_empty"), VALUES)[0] is True


def test_evaluate_refuses_nonsense_comparisons() -> None:
    with pytest.raises(UnresolvedReference, match="as numbers"):
        evaluate(_condition({"ref": "pdf_path"}, "gt", {"value": 1}), VALUES)
    with pytest.raises(UnresolvedReference, match="can't count"):
        evaluate(_condition({"count": "summary.error_count"}, "eq", {"value": 1}), VALUES)


def test_a_saved_workflow_loads_back_unchanged() -> None:
    workflow = parse_workflow(_audit())
    saved = workflow.model_dump(mode="json")

    assert parse_workflow(saved) == workflow
    assert saved["steps"][2]["conditions"][0]["left"] == {"count": "matches"}
    assert saved["steps"][6]["when"]["right"] == {"value": False}
