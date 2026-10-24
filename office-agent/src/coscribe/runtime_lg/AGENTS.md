# runtime_lg: the agent loop (LangChain/LangGraph), its middleware and the MCP connectors

## Purpose
Owns building the agent graph from the tool factories and their risk metadata (`agent.py`), the
deferred tool list (`tool_deferral.py`), the chat model for a `provider:model` string and how hard
it thinks (`providers.py`, `effort.py`), approvals' helpers (`audit.py`, `auto_review.py`,
`exec_policy.py`), delegated sub-agents (`subagents.py`), the code module's entry point
(`code_agent.py`), hosted and local MCP connectors and their sign-in (`mcp.py`, `mcp_oauth.py`),
the run side of scheduled tasks and self-wakes (`scheduled_tasks.py`, `selfwake.py`), turning a
conversation into a skill (`skill_authoring.py`), the guard that keeps a write from using a Word, Excel or PowerPoint copy the person has since edited (`edit_guard.py`) and message-shape helpers (`messages.py`,
`tool_calls.py`). Does not own the turn itself, the approval cards or history
(`conversation/session.py`), the routes (`web/`), the tools (`tools/`) or the coordinator's
prompt (`coordinator.py`).

## Public interface
Agent and tools: `build_langgraph_agent`, `tool_name`, `SubAgentHost`, `SUBAGENT_MAX_STEPS`, `build_delegation_tools`, `build_review_work_tool`, `select_reviewer_tools`, `subagent_report`.
Approvals: `AuditDecision`, `AuditLog`, `AuditReason`, `AutoApproveReason`, `record_decision`, `redact_secrets`, `Verdict`, `review_action`, `recent_user_requests`, `ExecPolicy`, `EXEC_POLICY_TOOL_NAMES`, `load_exec_policy`, `CODE_APPROVAL_RISKS`. Models: `resolve_chat_model`, `for_conversation`, `keeps_tool_list_fixed`.
Connectors: `connect_mcp_tools_lg`, `connect_one_mcp_server_lg`, `McpServerConnection` (loaded on first use: eager import costs 250 to 360 ms at startup). Sign-in: `McpOAuth`, `LoopbackCallback`, `NeedsSignIn`, `CallbackPortBusy`, `APP_REDIRECT_URI`, `SIGN_IN_TIMEOUT_SECONDS`. Deferral: `bound_tool_names`, `build_search_tools_tool`, `build_use_tool_tool`, `unwrap_use_tool_call`. Code tasks: `CODE_TASK_TOOL`, `CodeTaskContext`, `build_code_task_tool`.
Runs: `execute_run`, `start_investigation`, `continue_workflow_run`, `fire_trigger_now`, `stop_run`, `poll_due_scheduled_tasks`, `reconcile_interrupted_runs`, `poll_due_wakes`, `SilentSocket`.
Skills and messages: `CheckpointConversations`, `SkillSaveProposal`, `propose_skill_save_lg`, `write_skill_lg`, `extract_text`, `is_steer`, `render_transcript_lg`, `serialize_history_for_ws_lg`, `strip_mode_note`, `split_attached_files`, `tool_result_value`, `context_note`, `current_date_note`, `language_note`, `artifact_note`, `last_conversation_context`, `ACCEPT_EDITS_MODE_NOTE`, `AUTO_MODE_NOTE`, `NORMAL_MODE_NOTE`, `PLAN_MODE_NOTE`, `answer_every_tool_call`, `ORPHANED_TOOL_CALL_NOTE`.
Callers import these from `coscribe.runtime_lg`, not a submodule.

## Depends on
`coscribe.code_runtime`, `coscribe.runtime`, `coscribe.tools`, `coscribe.workflows`.

## Invariants
- `tool_deferral.py` and `mcp_oauth.py` are invariant paths (`office-agent/CLAUDE.md`): a change
  needs an independent review.
- The tool list bound to the model is the same for every conversation: the core tools plus
  `search_tools` and `use_tool`. Per-conversation data arrives in a tool result or a note on the
  user's message, never in a schema or the system prompt (`docs/decisions/0001-fixed-cached-tool-list.md`,
  `tests/test_tool_deferral.py`). Approvals, hooks and the audit log see the real tool name.
- A hosted connector signs in once, in the user's own browser (OAuth 2.1 with PKCE); this package
  ships no shared client secret (`docs/decisions/0004-connectors-sign-in-once-in-the-browser.md`).
- Approvals are not a sandbox: a script runs as the user (`docs/decisions/0002-approvals-not-a-sandbox.md`).
  An `ExecPolicy` rule can only allow, forbid or fall back to asking, never run anything.
- `runtime_lg` sits above `tools` and `runtime` and below `conversation`: it imports neither
  `conversation` nor `web`. A history never holds a tool call without a result, or the
  provider answers 400 (`answer_every_tool_call`).

## Tests
`tests/test_tool_deferral.py`, `tests/test_mcp_lg.py`, `tests/test_mcp_tools.py`,
`tests/test_runtime_lg_*.py`, `tests/test_effort.py`, `tests/test_audit_log.py`,
`tests/test_exec_policy.py`, `tests/test_subagent_tasks.py`, `tests/test_code_agent.py`,
`tests/test_scheduled_runs_lg.py`, `tests/test_selfwake_resume.py`; the sign-in through the
server is in `tests/web/test_connectors.py`.

## Verify by hand
A hosted connector's sign-in with a real account (Notion, Linear, Google Workspace); a long
conversation on a real provider to see the prompt cache hold across tool calls.

## Reviewed
2026-10-14 (first card).
