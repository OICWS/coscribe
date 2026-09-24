"""A workflow's definition: named inputs and an ordered list of steps, each
one of a few kinds with deliberately little autonomy. Stored as JSON on the
scheduled task that runs it.

Every value a step reads from an earlier one goes through a reference that
validate() checks against what's actually been produced by that point, so
a typo or a reordered step fails when the workflow is saved, not halfway
through a run."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_serializer,
    model_validator,
)

from .refs import template_references

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

FieldType = Literal["text", "number", "boolean", "list"]
CheckOp = Literal["eq", "ne", "gt", "ge", "lt", "le", "contains", "not_empty"]


def _identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"{value!r} must start with a lowercase letter and use only a-z, 0-9 and _ "
            "(at most 40 characters)"
        )
    return value


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowInput(_Model):
    name: str
    label: str = ""
    type: Literal["text", "file", "number"] = "text"
    default: str | float | None = None

    _check_name = field_validator("name")(_identifier)


class OutputField(_Model):
    name: str
    type: FieldType = "text"
    description: str = ""

    _check_name = field_validator("name")(_identifier)


class Operand(_Model):
    """Exactly one of: `ref` (a value by reference, e.g. "summary.error_count"),
    `count` (the length of a referenced list/text), or `value` (a literal)."""

    ref: str | None = None
    count: str | None = None
    value: Any = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Operand:
        if len(self.model_fields_set) != 1:
            raise ValueError("an operand is exactly one of ref, count or value")
        return self

    @model_serializer
    def _only_the_one_set(self) -> dict[str, Any]:
        # The unset two would come back as explicit nulls and fail
        # _exactly_one when a saved workflow is loaded again.
        key = next(iter(self.model_fields_set))
        return {key: getattr(self, key)}

    def reference(self) -> str | None:
        return self.ref if self.ref is not None else self.count


class Condition(_Model):
    left: Operand
    op: CheckOp
    right: Operand | None = None

    @model_validator(mode="after")
    def _right_unless_unary(self) -> Condition:
        if (self.op == "not_empty") != (self.right is None):
            raise ValueError("not_empty takes no right-hand side; every other op needs one")
        return self


class _Step(_Model):
    id: str
    title: str = Field(min_length=1, max_length=120)

    _check_id = field_validator("id")(_identifier)


class ToolStep(_Step):
    kind: Literal["tool"] = "tool"
    tool: str
    args: dict[str, Any] = {}
    save_as: str | None = None

    _check_save_as = field_validator("save_as")(lambda v: v if v is None else _identifier(v))


class ScriptStep(_Step):
    """A fixed Python script, reviewed once when the workflow is saved. It
    reads earlier values from a ready-made `inputs` dict (never spliced into
    the code), and its result is the JSON it prints on its last line."""

    kind: Literal["script"] = "script"
    code: str = Field(min_length=1)
    inputs: dict[str, Any] = {}
    save_as: str | None = None

    _check_save_as = field_validator("save_as")(lambda v: v if v is None else _identifier(v))


class LLMStep(_Step):
    """One model call, no tools, that must return exactly `fields`."""

    kind: Literal["llm"] = "llm"
    prompt: str = Field(min_length=1)
    fields: list[OutputField] = Field(min_length=1)
    model: str | None = None
    save_as: str

    _check_save_as = field_validator("save_as")(_identifier)


class CheckStep(_Step):
    """Every condition must hold, or the run stops here."""

    kind: Literal["check"] = "check"
    conditions: list[Condition] = Field(min_length=1)


class ApprovalStep(_Step):
    """Pause until a person approves -- only when `when` holds, if given."""

    kind: Literal["approval"] = "approval"
    message: str = Field(min_length=1)
    when: Condition | None = None


Step = Annotated[
    ToolStep | ScriptStep | LLMStep | CheckStep | ApprovalStep, Field(discriminator="kind")
]


def _root(reference: str) -> str:
    return reference.split(".", 1)[0]


def _condition_refs(condition: Condition) -> list[str]:
    operands = [condition.left] + ([condition.right] if condition.right else [])
    return [r for o in operands if (r := o.reference()) is not None]


def step_references(step: Step) -> list[str]:
    """Every reference a step reads, as written."""
    if isinstance(step, ToolStep):
        return _value_references(step.args)
    if isinstance(step, ScriptStep):
        return _value_references(step.inputs)
    if isinstance(step, LLMStep):
        return template_references(step.prompt)
    if isinstance(step, CheckStep):
        return [r for c in step.conditions for r in _condition_refs(c)]
    if isinstance(step, ApprovalStep):
        refs = template_references(step.message)
        return refs + (_condition_refs(step.when) if step.when else [])
    return []  # pragma: no cover -- every kind is handled above


def _value_references(value: Any) -> list[str]:
    if isinstance(value, str):
        return template_references(value)
    if isinstance(value, list):
        return [r for item in value for r in _value_references(item)]
    if isinstance(value, dict):
        return [r for item in value.values() for r in _value_references(item)]
    return []


def step_output(step: Step) -> str | None:
    return getattr(step, "save_as", None)


class Workflow(_Model):
    version: Literal[1] = 1
    inputs: list[WorkflowInput] = []
    # Empty is a workflow still being built on its task page; preflight
    # refuses to run one.
    steps: list[Step] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _names_resolve_in_order(self) -> Workflow:
        step_ids: set[str] = set()
        available = {i.name for i in self.inputs}
        if len(available) != len(self.inputs):
            raise ValueError("input names must be unique")
        for index, step in enumerate(self.steps, start=1):
            if step.id in step_ids:
                raise ValueError(f"step {index}: id {step.id!r} is used twice")
            step_ids.add(step.id)
            for reference in step_references(step):
                if _root(reference) not in available:
                    raise ValueError(
                        f"step {index} ({step.title!r}) reads {reference!r}, which isn't an "
                        "input or the result of an earlier step"
                    )
            output = step_output(step)
            if output is not None:
                if output in available:
                    raise ValueError(f"step {index}: {output!r} is already taken")
                available.add(output)
        return self


def parse_workflow(data: Any) -> Workflow:
    return Workflow.model_validate(data)


def workflow_error(exc: ValidationError) -> str:
    """One readable line per problem, located by step number."""
    lines = []
    for error in exc.errors():
        loc = list(error["loc"])
        where = ""
        if len(loc) >= 2 and loc[0] == "steps" and isinstance(loc[1], int):
            where = f"step {loc[1] + 1}: "
            loc = loc[2:]
            if loc and isinstance(loc[0], str) and loc[0] in _STEP_KINDS:
                loc = loc[1:]
        field = ".".join(str(part) for part in loc)
        message = str(error["msg"]).removeprefix("Value error, ")
        lines.append(f"{where}{field + ': ' if field else ''}{message}")
    return "; ".join(lines)


_STEP_KINDS = frozenset({"tool", "script", "llm", "check", "approval"})
