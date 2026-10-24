# tools.bookkeeping: the model's task list, scheduled tasks, runs, wakes, messages and memory

## Purpose
Owns the tools that keep the model's own state, and the stores behind them: the task list and
plan drafts (`tasks.py`), scheduled tasks and their triggers (`scheduled_tasks.py`), sub-agent
runs (`subagent_tasks.py`), background commands (`background_tasks.py`), self-wakes and signals
(`selfwake.py`), messages between conversations (`conversations.py`), questions and plan choices
(`interaction.py`) and the memory file (`memory.py`). Does not own running a scheduled task or
resuming a wake (`runtime_lg/scheduled_tasks.py`, `runtime_lg/selfwake.py`), the scripts a
background command starts (`tools/exec`) or the routes that show these (`web/routes/`).

## Public interface
Factories: `build_task_tools`, `build_scheduled_task_tools`, `build_subagent_task_tools`,
`build_background_task_tools`, `build_selfwake_tools`, `build_conversation_tools`,
`build_interaction_tools`. Stores and records: `BackgroundTask`, `BackgroundTaskStore`,
`ScheduleRule`, `ScheduledTrigger`, `ScheduledTriggerStore`, `ScheduledRun`, `SubAgentTask`,
`SubAgentTaskStore`, `WakeRequest`, `WakeStore`, `SignalStore`, `MessagingStore`. Scheduled
triggers: `SCHEDULED_THREAD_PREFIX`, `TASK_DRAFT_TOOL_NAMES`, `compute_next_run_at`,
`parse_run_thread_id`, `create_trigger`, `update_trigger`, `patch_trigger`, `record_draft`,
`edit_draft`. Sub-agents: `running_subagent`, `get_subagent_transcript`, `stop_subagent_task`,
`forget_finished_subagents`. Questions, plans and messages: `QUESTION_TOOL_NAMES`,
`PLAN_CHOICE_AUTO`, `PLAN_CHOICE_MANUAL`, `PLAN_CHOICE_REVISE`, `PLAN_CHOICE_REJECT`, `format_question_answers`,
`normalize_questions`, `format_incoming`. Memory: `load_memory`, `format_memory_section`.
Also exported: `FINISHED_STATUSES`, `ConversationSource`, `ConversationTranscript`, `MessagingError`, `TaskToolkit`, `register_subagent_run`,
`take_stop_requester`, `set_change_listener`, `set_inbox_listener`, `adopt_running_command`,
`forget_finished_background_tasks`, `read_background_log`, `stop_background_task`. Callers outside
`tools` reach all of these through `coscribe.tools`.

## Depends on
`coscribe.runtime`, `coscribe.tools._file_locks`, `coscribe.tools.exec`, `coscribe.workflow_spec`.

## Invariants
- A store is a file under the state directory written through `_file_locks`; an id that comes
  from a URL must never name a path outside its store.
- `scheduled_tasks` validates a workflow against `coscribe.workflow_spec`, a package below `tools`,
  so nothing in `tools` imports `workflows`; a fixed workflow run never reaches an agent.
- Tool schemas are fixed and snapshotted (`tests/tool_schemas.txt`).

## Tests
`tests/test_task_tools.py`, `tests/test_scheduled_tasks_tool.py`, `tests/test_scheduled_runs_lg.py`,
`tests/test_subagent_tasks.py`, `tests/test_background_tasks.py`, `tests/test_selfwake_tool.py`,
`tests/test_conversations_tool.py`, `tests/test_memory_tool.py`.

## Verify by hand
A scheduled task firing while the app is closed and reopened; a background command's output in
the panel on Windows.

## Reviewed
2026-10-17 (first card).
