"""Phase 1 spike: evaluating a LangGraph-based replacement for `runtime/` +
the vendored Gemini provider. Not imported by cli.py/web/coordinator.py yet
-- see the migration plan for scope and exit criteria."""

from typing import TYPE_CHECKING

from .agent import build_langgraph_agent, tool_name
from .audit import AuditLog, AutoApproveReason, record_decision, redact_secrets
from .audit import Decision as AuditDecision
from .audit import Reason as AuditReason
from .auto_review import Verdict, recent_user_requests, review_action
from .code_agent import CODE_APPROVAL_RISKS, CODE_TASK_TOOL, CodeTaskContext, build_code_task_tool
from .conversation_source import CheckpointConversations
from .exec_policy import EXEC_POLICY_TOOL_NAMES, ExecPolicy, load_exec_policy
from .mcp_oauth import (
    APP_REDIRECT_URI,
    SIGN_IN_TIMEOUT_SECONDS,
    CallbackPortBusy,
    LoopbackCallback,
    McpOAuth,
    NeedsSignIn,
)
from .messages import (
    ACCEPT_EDITS_MODE_NOTE,
    AUTO_MODE_NOTE,
    NORMAL_MODE_NOTE,
    PLAN_MODE_NOTE,
    artifact_note,
    context_note,
    current_date_note,
    extract_text,
    is_steer,
    language_note,
    last_conversation_context,
    render_transcript_lg,
    serialize_history_for_ws_lg,
    split_attached_files,
    strip_mode_note,
    tool_result_value,
)
from .providers import for_conversation, keeps_tool_list_fixed, resolve_chat_model
from .scheduled_tasks import (
    continue_workflow_run,
    execute_run,
    fire_trigger_now,
    poll_due_scheduled_tasks,
    reconcile_interrupted_runs,
    start_investigation,
    stop_run,
)
from .selfwake import SilentSocket, poll_due_wakes
from .skill_authoring import SkillSaveProposal, propose_skill_save_lg, write_skill_lg
from .subagents import (
    SUBAGENT_MAX_STEPS,
    SubAgentHost,
    build_delegation_tools,
    build_review_work_tool,
    select_reviewer_tools,
    subagent_report,
)
from .tool_calls import ORPHANED_TOOL_CALL_NOTE, answer_every_tool_call
from .tool_deferral import (
    bound_tool_names,
    build_search_tools_tool,
    build_use_tool_tool,
    unwrap_use_tool_call,
)

if TYPE_CHECKING:
    from .mcp import McpServerConnection, connect_mcp_tools_lg, connect_one_mcp_server_lg

__all__ = [
    "ACCEPT_EDITS_MODE_NOTE",
    "APP_REDIRECT_URI",
    "AUTO_MODE_NOTE",
    "CODE_TASK_TOOL",
    "CallbackPortBusy",
    "CheckpointConversations",
    "CodeTaskContext",
    "LoopbackCallback",
    "McpOAuth",
    "McpServerConnection",
    "NORMAL_MODE_NOTE",
    "NeedsSignIn",
    "PLAN_MODE_NOTE",
    "SIGN_IN_TIMEOUT_SECONDS",
    "bound_tool_names",
    "build_code_task_tool",
    "build_search_tools_tool",
    "build_use_tool_tool",
    "context_note",
    "current_date_note",
    "artifact_note",
    "language_note",
    "last_conversation_context",
    "unwrap_use_tool_call",
    "ORPHANED_TOOL_CALL_NOTE",
    "answer_every_tool_call",
    "is_steer",
    "SUBAGENT_MAX_STEPS",
    "SilentSocket",
    "AuditDecision",
    "AuditLog",
    "AuditReason",
    "AutoApproveReason",
    "CODE_APPROVAL_RISKS",
    "EXEC_POLICY_TOOL_NAMES",
    "ExecPolicy",
    "build_langgraph_agent",
    "SubAgentHost",
    "Verdict",
    "build_delegation_tools",
    "build_review_work_tool",
    "select_reviewer_tools",
    "connect_mcp_tools_lg",
    "connect_one_mcp_server_lg",
    "continue_workflow_run",
    "extract_text",
    "execute_run",
    "fire_trigger_now",
    "load_exec_policy",
    "poll_due_scheduled_tasks",
    "poll_due_wakes",
    "recent_user_requests",
    "reconcile_interrupted_runs",
    "record_decision",
    "start_investigation",
    "stop_run",
    "redact_secrets",
    "SkillSaveProposal",
    "propose_skill_save_lg",
    "render_transcript_lg",
    "for_conversation",
    "keeps_tool_list_fixed",
    "resolve_chat_model",
    "subagent_report",
    "review_action",
    "serialize_history_for_ws_lg",
    "split_attached_files",
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
    if name == "McpServerConnection":
        from .mcp import McpServerConnection

        return McpServerConnection
    if name == "connect_one_mcp_server_lg":
        from .mcp import McpServerConnection, connect_one_mcp_server_lg

        return connect_one_mcp_server_lg
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
