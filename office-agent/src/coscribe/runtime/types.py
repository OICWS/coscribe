"""Core serializable types for the agent runtime: ToolMetadata (risk
classification every tool, built-in or MCP, carries -- reused as-is by
runtime_lg, see runtime_lg/agent.py) and Agent (the declarative agent
definition coordinator.py builds).

This module used to also hold RunResult/RunState/RunStep (a full run's
message history + execution trace, serializable for the old runtime's
FileStateStore persistence) -- deleted as dead code once runtime_lg's own
checkpointer-backed persistence became the only real state-persistence
path left running. See runtime_lg/README.md's "audit + delete old
runtime" section.

Design is modeled on aisuite's (unreleased, main-branch-only) `agents`
module, reimplemented here because that module is not part of the
published aisuite package we depend on (see ARCHITECTURE.md).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

# ROADMAP.md's Phase 4 risk taxonomy, replacing the old plain "low/medium/
# high" risk_level + independently-set requires_approval bool: a tool's
# *kind* of risk now determines whether it gets gated, rather than two
# separate, driftable axes needing to agree with each other by hand at
# every one of this package's ~25 tool_metadata(...) call sites.
#
# READ: no side effects, whether the data read is local (read_file) or
#   external (web_search, search_images -- reaching out to the internet
#   isn't itself a side effect, since nothing is written anywhere).
# WRITE_LOCAL: side effects confined to this machine (write_file,
#   write_pptx, task_create, remember, ...).
# EXEC: arbitrary code execution -- run_python_script/run_node_script (see
#   tools/scripts.py's docstring for the "no sandbox, the approval prompt
#   IS the safety mechanism" posture this implies). Real tools exist here
#   today -- this was added deliberately for the pptx skill work, reversing
#   the earlier "decided against" stance an older draft of this taxonomy's
#   own planning doc still described; not a hypothetical placeholder.
# EXTERNAL: side effects that reach outside this machine -- any MCP tool
#   (an arbitrary, opaque external server; runtime_lg/mcp.py classifies
#   every one this way, since coscribe can't introspect what a given MCP
#   tool actually does) and download_image (fetches attacker-influenceable
#   content from an arbitrary URL, distinct in kind from a pure local
#   write). Gated tighter than WRITE_LOCAL once Phase 4's unattended/
#   selfwake work lands (not yet -- today both are gated identically).
ToolRiskCategory = Literal["READ", "WRITE_LOCAL", "EXEC", "EXTERNAL"]

# Every category except READ has a real side effect worth a human's eyes
# on it first -- this is the single source of truth requires_approval now
# derives from, instead of being set (and capable of silently drifting
# out of sync with risk_category) independently per tool.
_CATEGORIES_REQUIRING_APPROVAL: frozenset[ToolRiskCategory] = frozenset(
    {"WRITE_LOCAL", "EXEC", "EXTERNAL"}
)


@dataclass
class ToolMetadata:
    """Risk classification attached to a tool, used to gate execution."""

    risk_category: ToolRiskCategory = "READ"
    category: str | None = None
    description: str | None = None

    @property
    def requires_approval(self) -> bool:
        return self.risk_category in _CATEGORIES_REQUIRING_APPROVAL


TOOL_METADATA_ATTR = "__coscribe_tool_metadata__"


def tool_metadata(
    func: Callable[..., Any],
    *,
    risk_category: ToolRiskCategory = "READ",
    category: str | None = None,
    description: str | None = None,
) -> Callable[..., Any]:
    """Attach a ToolMetadata to a callable, returning it unchanged (for use as a wrapper)."""
    setattr(
        func,
        TOOL_METADATA_ATTR,
        ToolMetadata(
            risk_category=risk_category,
            category=category,
            description=description,
        ),
    )
    return func


def get_tool_metadata(func: Callable[..., Any]) -> ToolMetadata:
    return getattr(func, TOOL_METADATA_ATTR, ToolMetadata())


@dataclass(kw_only=True)
class Agent:
    """Declarative agent definition: identity, model, prompt, and available tools."""

    name: str
    model: str
    instructions: str | None = None
    tools: list[Callable[..., Any]] = field(default_factory=list)
    model_settings: dict[str, Any] = field(default_factory=dict)
