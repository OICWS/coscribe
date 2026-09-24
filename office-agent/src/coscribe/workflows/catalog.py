"""A tool's parameters as the workflow editor needs them to build a tool
step: name, a coarse type for picking an input control, whether it's
required, its default, and the one-line meaning from the docstring's
Args section."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any, Literal

from langchain_core.tools import BaseTool

ParamType = Literal["text", "number", "boolean", "other"]

_ARG_LINE = re.compile(r"^(\s+)([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?:\s*(.*)$")


def _param_type(annotation: Any) -> ParamType:
    # Annotations are strings under `from __future__ import annotations`;
    # the name is all that's needed to pick a control.
    if isinstance(annotation, str):
        text = annotation
    elif isinstance(annotation, type):
        text = annotation.__name__
    else:
        text = str(annotation)  # typing constructs: "typing.Optional[int]"
    if re.search(r"\b(list|dict|List|Dict|Sequence|Mapping)\b", text):
        return "other"
    if re.search(r"\bbool\b", text):
        return "boolean"
    if re.search(r"\b(int|float)\b", text):
        return "number"
    if re.search(r"\bstr\b", text):
        return "text"
    return "other"


def arg_descriptions(doc: str) -> dict[str, str]:
    """{name: meaning} from a Google-style `Args:` section, continuation
    lines joined."""
    descriptions: dict[str, str] = {}
    in_args = False
    arg_indent: int | None = None
    current: str | None = None
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped == "Args:":
            in_args, arg_indent, current = True, None, None
            continue
        if not in_args:
            continue
        if not stripped:
            current = None
            continue
        indent = len(line) - len(line.lstrip())
        match = _ARG_LINE.match(line)
        if match and (arg_indent is None or indent == arg_indent):
            arg_indent = indent
            current = match.group(2)
            descriptions[current] = match.group(3).strip()
        elif current is not None and arg_indent is not None and indent > arg_indent:
            descriptions[current] = f"{descriptions[current]} {stripped}".strip()
        else:
            in_args = False
    return descriptions


_JSON_TYPES: dict[str, ParamType] = {
    "string": "text",
    "number": "number",
    "integer": "number",
    "boolean": "boolean",
}


def _json_param_type(schema: dict[str, Any]) -> ParamType:
    kind = schema.get("type")
    if isinstance(kind, str):
        return _JSON_TYPES.get(kind, "other")
    # Optional values arrive as anyOf [{type: X}, {type: null}].
    options = [o for o in schema.get("anyOf", []) if o.get("type") != "null"]
    return _json_param_type(options[0]) if len(options) == 1 else "other"


def _schema_params(tool: Any) -> list[dict[str, Any]]:
    """A LangChain tool's parameters (a connector's, whose schema is JSON
    schema rather than a Python signature)."""
    schema = tool.args_schema
    if schema is not None and not isinstance(schema, dict):
        schema = schema.model_json_schema()
    properties = (schema or {}).get("properties") or tool.args or {}
    required = set((schema or {}).get("required") or [])
    params = []
    for name, spec in properties.items():
        default = spec.get("default")
        params.append(
            {
                "name": name,
                "type": _json_param_type(spec),
                "required": name in required,
                "default": default if isinstance(default, (str, int, float, bool)) else None,
                "description": " ".join(str(spec.get("description", "")).split()),
            }
        )
    return params


def tool_description(tool: Any) -> str:
    """The first paragraph of what a tool says it does."""
    text = tool.description if isinstance(tool, BaseTool) else inspect.getdoc(tool) or ""
    return " ".join((text or "").split("\n\n", 1)[0].split())


def describe_params(tool: Callable[..., Any] | BaseTool) -> list[dict[str, Any]]:
    if isinstance(tool, BaseTool):
        return _schema_params(tool)
    signature = inspect.signature(tool)
    meanings = arg_descriptions(inspect.getdoc(tool) or "")
    params = []
    for name, param in signature.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        required = param.default is inspect.Parameter.empty
        default = None if required else param.default
        params.append(
            {
                "name": name,
                "type": _param_type(param.annotation),
                "required": required,
                "default": default if isinstance(default, (str, int, float, bool)) else None,
                "description": meanings.get(name, ""),
            }
        )
    return params
