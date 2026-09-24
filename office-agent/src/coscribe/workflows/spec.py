"""A workflow's definition: named inputs and an ordered list of steps, each
one of a few kinds with deliberately little autonomy. Stored as JSON on the
scheduled task that runs it.

Every value a step reads from an earlier one goes through a reference that
validate() checks against what's actually been produced by that point, so
a typo or a reordered step fails when the workflow is saved, not halfway
through a run.

Branches and loops hold their own step lists, and scope what they
produce: after a branch, only a name both arms produce is certain to
exist; a loop body's names are per item, so only the loop's collected
list is visible after it. Steps are numbered in document order, nested
ones included -- the numbers the editor and the run view show."""

from __future__ import annotations

import re
from dataclasses import dataclass
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

from .refs import is_reference, malformed_templates, template_references

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


MAX_NESTING = 3
MAX_STEPS = 100
MAX_LOOP_ITEMS = 200


class BranchStep(_Step):
    """Runs `then` when the condition holds, else `otherwise`."""

    kind: Literal["branch"] = "branch"
    condition: Condition
    then: list[Step] = []
    otherwise: list[Step] = []


class LoopStep(_Step):
    """Runs `steps` once per item of the list `over`, in order, with the
    item as `item`. `save_as` gets the list of each pass's `collect`."""

    kind: Literal["loop"] = "loop"
    over: str
    item: str = "item"
    steps: list[Step] = []
    collect: str | None = None
    save_as: str | None = None
    # A longer list fails the loop rather than being cut short.
    max_items: int = Field(default=50, ge=1, le=MAX_LOOP_ITEMS)

    _check_item = field_validator("item")(_identifier)
    _check_save_as = field_validator("save_as")(lambda v: v if v is None else _identifier(v))

    @field_validator("over", "collect")
    @classmethod
    def _a_reference(cls, value: str | None) -> str | None:
        if value is not None and not is_reference(value):
            raise ValueError(f"{value!r} isn't a name like matches or report.rows")
        return value

    @model_validator(mode="after")
    def _collects_what_it_saves(self) -> LoopStep:
        if (self.collect is None) != (self.save_as is None):
            raise ValueError(
                "collect and save_as go together: what to keep from each pass, "
                "and the name for the list"
            )
        return self


Step = Annotated[
    ToolStep | ScriptStep | LLMStep | CheckStep | ApprovalStep | BranchStep | LoopStep,
    Field(discriminator="kind"),
]
BlockStep = BranchStep | LoopStep


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
    if isinstance(step, BranchStep):
        return _condition_refs(step.condition)
    if isinstance(step, LoopStep):
        return [step.over]
    return []  # pragma: no cover -- every kind is handled above


def _value_references(value: Any) -> list[str]:
    if isinstance(value, str):
        return template_references(value)
    if isinstance(value, list):
        return [r for item in value for r in _value_references(item)]
    if isinstance(value, dict):
        return [r for item in value.values() for r in _value_references(item)]
    return []


def _texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [t for item in value for t in _texts(item)]
    if isinstance(value, dict):
        return [t for item in value.values() for t in _texts(item)]
    return []


def step_templates(step: Step) -> list[str]:
    """Every text of a step that references are filled into."""
    if isinstance(step, ToolStep):
        return _texts(step.args)
    if isinstance(step, ScriptStep):
        return _texts(step.inputs)
    if isinstance(step, LLMStep):
        return [step.prompt]
    if isinstance(step, ApprovalStep):
        return [step.message]
    return []


def step_output(step: Step) -> str | None:
    return getattr(step, "save_as", None)


def child_lists(step: Step) -> list[tuple[str, list[Step]]]:
    """A block's own step lists, in document order."""
    if isinstance(step, BranchStep):
        return [("then", step.then), ("otherwise", step.otherwise)]
    if isinstance(step, LoopStep):
        return [("steps", step.steps)]
    return []


@dataclass(frozen=True)
class Placed:
    """A step with where it sits: its document-order number and the ids of
    the loops around it, outermost first."""

    number: int
    step: Step
    loops: tuple[str, ...]


def walk(steps: list[Step]) -> list[Placed]:
    placed: list[Placed] = []

    def visit(items: list[Step], loops: tuple[str, ...]) -> None:
        for step in items:
            placed.append(Placed(len(placed) + 1, step, loops))
            inner = (*loops, step.id) if isinstance(step, LoopStep) else loops
            for _arm, children in child_lists(step):
                visit(children, inner)

    visit(steps, ())
    return placed


class _Scope:
    """Save-time checks, walking the steps in the order they'd run."""

    def __init__(self, inputs: list[WorkflowInput]) -> None:
        self.number = 0
        self.ids: set[str] = set()
        self.taken = {i.name for i in inputs}
        # Names that exist only inside a loop or one arm, for a clearer
        # message when a later step reads one.
        self.scoped: set[str] = set()
        # A model step's result is exactly its declared fields.
        self.fields: dict[str, set[str]] = {}

    def _field(self, reference: str, where: str) -> None:
        root, _, rest = reference.partition(".")
        fields = self.fields.get(root)
        field_name = rest.split(".", 1)[0]
        if fields is not None and rest and field_name not in fields:
            raise ValueError(
                f"{where} reads {reference!r}, but {root} only has " + ", ".join(sorted(fields))
            )

    def check(self, steps: list[Step], available: set[str], depth: int) -> set[str]:
        """Returns the names available after `steps`."""
        available = set(available)
        for step in steps:
            self.number += 1
            where = f"step {self.number} ({step.title!r})"
            if self.number > MAX_STEPS:
                raise ValueError(f"a workflow has at most {MAX_STEPS} steps, nested ones included")
            if step.id in self.ids:
                raise ValueError(f"step {self.number}: id {step.id!r} is used twice")
            self.ids.add(step.id)
            for text in step_templates(step):
                for bad in malformed_templates(text):
                    raise ValueError(
                        f"{where}: {bad} isn't a reference -- write "
                        "{{name}} or {{name.field}}, with no other expressions"
                    )
            for reference in step_references(step):
                if _root(reference) not in available:
                    raise ValueError(
                        f"{where} reads {reference!r}, which isn't an input or the result of "
                        "an earlier step" + _scope_hint(reference, self.scoped)
                    )
                self._field(reference, where)
            if isinstance(step, (BranchStep, LoopStep)) and depth >= MAX_NESTING:
                raise ValueError(f"{where}: branches and loops nest at most {MAX_NESTING} deep")
            if isinstance(step, BranchStep):
                available |= self._branch(step, available, depth)
                continue
            if isinstance(step, LoopStep):
                self._loop(step, where, available, depth)
            output = step_output(step)
            if output is not None:
                if output in self.taken:
                    raise ValueError(f"step {self.number}: {output!r} is already taken")
                self.taken.add(output)
                available.add(output)
                if isinstance(step, LLMStep):
                    self.fields[output] = {f.name for f in step.fields}
        return available

    def _branch(self, step: BranchStep, available: set[str], depth: int) -> set[str]:
        # Each arm may produce the same names -- that's how a value set
        # either way is read after the branch.
        before = set(self.taken)
        self.taken = set(before)
        after_then = self.check(step.then, available, depth + 1)
        taken_then, self.taken = self.taken, set(before)
        after_otherwise = self.check(step.otherwise, available, depth + 1)
        self.taken |= taken_then
        merged = (after_then - available) & (after_otherwise - available)
        self.scoped |= ((after_then | after_otherwise) - available) - merged
        return merged

    def _loop(self, step: LoopStep, where: str, available: set[str], depth: int) -> None:
        if step.item in self.taken:
            raise ValueError(f"{where}: {step.item!r} is already taken, name the item differently")
        # A body's names exist per item, so they're free again after the
        # loop -- two loops in a row can both call their item `item`.
        before = set(self.taken)
        self.taken.add(step.item)
        after_body = self.check(step.steps, available | {step.item}, depth + 1)
        if step.collect is not None and _root(step.collect) in after_body - available:
            self._field(step.collect, where)
        if step.collect is not None and _root(step.collect) not in after_body - available:
            raise ValueError(
                f"{where} collects {step.collect!r}, which isn't the item or a result of "
                "a step inside the loop"
            )
        self.scoped |= self.taken - before
        self.taken = before


def _scope_hint(reference: str, scoped: set[str]) -> str:
    if _root(reference) in scoped:
        return " here (it's made inside a loop, or in only one arm of a branch)"
    return ""


class Workflow(_Model):
    version: Literal[1] = 1
    inputs: list[WorkflowInput] = []
    # Empty is a workflow still being built on its task page; preflight
    # refuses to run one.
    steps: list[Step] = Field(default_factory=list, max_length=MAX_STEPS)

    @model_validator(mode="after")
    def _names_resolve_in_order(self) -> Workflow:
        if len({i.name for i in self.inputs}) != len(self.inputs):
            raise ValueError("input names must be unique")
        _Scope(self.inputs).check(self.steps, {i.name for i in self.inputs}, 0)
        return self


def parse_workflow(data: Any) -> Workflow:
    return Workflow.model_validate(data)


def workflow_error(exc: ValidationError, data: Any = None) -> str:
    """One readable line per problem, located by step number (document
    order, as the editor numbers steps -- which takes the submitted `data`
    for a problem inside a branch or loop)."""
    lines = []
    for error in exc.errors():
        number, loc = _locate(data, list(error["loc"]))
        where = f"step {number}: " if number is not None else ""
        field = ".".join(str(part) for part in loc)
        message = str(error["msg"]).removeprefix("Value error, ")
        lines.append(f"{where}{field + ': ' if field else ''}{message}")
    return "; ".join(lines)


_STEP_KINDS = frozenset({"tool", "script", "llm", "check", "approval", "branch", "loop"})
_ARMS = ("then", "otherwise", "steps")


def _count(step: Any) -> int:
    if not isinstance(step, dict):
        return 1
    return 1 + sum(_count(child) for arm in _ARMS for child in _as_list(step.get(arm)))


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _locate(data: Any, loc: list[Any]) -> tuple[int | None, list[Any]]:
    if len(loc) < 2 or loc[0] != "steps" or not isinstance(loc[1], int):
        return None, loc
    steps = _as_list(data.get("steps")) if isinstance(data, dict) else []
    number = 0
    loc = loc[1:]
    while loc and isinstance(loc[0], int):
        index = loc[0]
        number += sum(_count(s) for s in steps[:index]) + 1
        step = steps[index] if index < len(steps) else None
        loc = loc[1:]
        if loc and loc[0] in _STEP_KINDS:
            loc = loc[1:]
        if len(loc) >= 2 and loc[0] in _ARMS and isinstance(loc[1], int) and isinstance(step, dict):
            if loc[0] == "otherwise":
                number += sum(_count(s) for s in _as_list(step.get("then")))
            steps = _as_list(step.get(loc[0]))
            loc = loc[1:]
            continue
        break
    return number, loc


BranchStep.model_rebuild()
LoopStep.model_rebuild()
Workflow.model_rebuild()
