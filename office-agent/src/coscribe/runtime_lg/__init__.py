"""Phase 1 spike: evaluating a LangGraph-based replacement for `runtime/` +
the vendored Gemini provider. Not imported by cli.py/web/coordinator.py yet
-- see the migration plan for scope and exit criteria."""

from .agent import build_langgraph_agent, tool_name
from .audit import AuditLog, record_decision, redact_secrets
from .exec_policy import EXEC_POLICY_TOOL_NAMES, ExecPolicy, load_exec_policy
from .messages import (
    extract_text,
    render_transcript_lg,
    serialize_history_for_ws_lg,
    strip_mode_note,
    tool_result_value,
)
from .providers import resolve_chat_model
from .scheduled_tasks import poll_due_scheduled_tasks
from .selfwake import poll_due_wakes
from .skill_authoring import SkillSaveProposal, propose_skill_save_lg, write_skill_lg
from .subagents import build_review_work_tool, build_spawn_agent_tool
from .workflows import (
    infer_step_assertions_lg,
    propose_workflow_save_lg,
    record_agent_workflow_lg,
    record_chain_workflow_lg,
    recorded_tool_call_steps_lg,
    run_chain_lg,
)

__all__ = [
    "AuditLog",
    "EXEC_POLICY_TOOL_NAMES",
    "ExecPolicy",
    "build_langgraph_agent",
    "build_review_work_tool",
    "build_spawn_agent_tool",
    "connect_mcp_tools_lg",
    "extract_text",
    "infer_step_assertions_lg",
    "load_exec_policy",
    "poll_due_scheduled_tasks",
    "poll_due_wakes",
    "propose_workflow_save_lg",
    "record_agent_workflow_lg",
    "record_chain_workflow_lg",
    "record_decision",
    "recorded_tool_call_steps_lg",
    "redact_secrets",
    "SkillSaveProposal",
    "propose_skill_save_lg",
    "render_transcript_lg",
    "resolve_chat_model",
    "run_chain_lg",
    "serialize_history_for_ws_lg",
    "strip_mode_note",
    "tool_name",
    "tool_result_value",
    "write_skill_lg",
]


def __getattr__(name: str) -> object:
    # `connect_mcp_tools_lg` pulls in langchain_mcp_adapters (and the mcp
    # SDK it drags in) -- measured at ~250-360ms of startup import time
    # even for a user with zero MCP servers configured, since a plain
    # `from .mcp import connect_mcp_tools_lg` above would run unconditionally
    # on every `import coscribe.runtime_lg`. PEP 562 module __getattr__
    # defers that import until something actually reaches for the name.
    if name == "connect_mcp_tools_lg":
        from .mcp import connect_mcp_tools_lg

        return connect_mcp_tools_lg
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
