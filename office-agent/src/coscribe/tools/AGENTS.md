# tools: the built-in tools the agent can call, and the stores behind them

## Purpose
Owns the tool layer's shared entry point and the tools that fit no smaller component: files and
folders (`files.py`), conversions (`convert.py`, `pdf_pages.py`), the connector config and
permissions (`mcp.py`, `connector_permissions.py`) and the skills and PowerPoint template
catalogs (`skills.py`, `skill_catalog.py`, `pptx_templates.py`). The rest is split by card:
Word, Excel, PowerPoint and PDF in `office/<format>/`, scripts in `exec/`, the browser, search,
HTTP and images in `web/`, the model's own state in `bookkeeping/`. Does not own the agent loop
or approvals (`runtime_lg/`), the routes (`web/`) or the coordinator's tool list (`coordinator.py`).

## Public interface
Factories: `build_file_tools`, `build_document_tools`, `build_spreadsheet_tools`,
`build_presentation_tools`, `build_convert_tools`, `build_image_tools`, `build_script_tools`,
`build_node_script_tools`, `build_background_task_tools`, `build_browser_tools`,
`build_websearch_tools`, `build_task_tools`, `build_skill_tools`, `build_interaction_tools`,
`build_selfwake_tools`, `build_scheduled_task_tools`, `build_subagent_task_tools`,
`build_conversation_tools`. Toolkits and stores: `DocumentToolkit`, `SpreadsheetToolkit`,
`BackgroundTask`, `BackgroundTaskStore`, `ScheduleRule`, `ScheduledTrigger`,
`ScheduledTriggerStore`, `ScheduledRun`, `SubAgentTask`, `SubAgentTaskStore`, `WakeRequest`,
`WakeStore`, `SignalStore`, `MessagingStore`, `DesignSystemStore`, `DesignSystemError`, `as_deck_design_system`, `build_design_system_tools` (the person's design-system library, decision 0020). Connectors: `validate_mcp_config`,
`load_mcp_server_configs`, `prepare_for_connect`, `name_of`, `is_read_only`, `connector_of`,
`apply_connector_permissions`, `ConnectorPermissions`. Skills and templates: `SkillInfo`,
`SkillUploadError`, `TemplateInfo`, `load_skills`, `load_builtin_skills`, `load_builtin_templates`,
`load_pptx_templates`, `save_uploaded_skill`, `slugify_skill_name`, `format_skill_listing`,
`format_template_listing`, `NEW_DECK_INSTRUCTIONS`. Memory, questions and plans: `load_memory`,
`format_memory_section`, `QUESTION_TOOL_NAMES`, `TASK_DRAFT_TOOL_NAMES`, `PLAN_CHOICE_AUTO`,
`PLAN_CHOICE_MANUAL`, `PLAN_CHOICE_REJECT`, `PLAN_CHOICE_REVISE`, `edit_draft`, `format_question_answers`,
`normalize_questions`. Runs and browser: `compute_next_run_at`, `parse_run_thread_id`,
`SCHEDULED_THREAD_PREFIX`, `format_incoming`, `running_subagent`, `get_subagent_transcript`,
`stop_subagent_task`, `forget_finished_subagents`, `BROWSER_HOST`, `BROWSER_RUNS`, `page_screenshot`. Folders, skills and environments: `WorkspaceScope`, `enabled_skill_names`, `ensure_script_env`, `venv_python`. Scheduled-trigger writes: `create_trigger`, `update_trigger`, `patch_trigger`, `record_draft`. Bookkeeping hooks and stores: `FINISHED_STATUSES`, `ConversationSource`, `ConversationTranscript`, `MessagingError`, `TaskToolkit`, `register_subagent_run`, `take_stop_requester`, `set_change_listener`, `set_inbox_listener`, `adopt_running_command`, `forget_finished_background_tasks`, `read_background_log`, `stop_background_task`. Connectors and skill catalog: `POLICIES`, `with_secrets`, `secret_names_used`, `SKILL_SAVE_CURATOR_INSTRUCTIONS`, `SKILL_SOURCE_MARKER`, `SkillCatalogError`, `all_skills`, `disabled_skill_names`, `get_plugin`, `install_catalog_skill`, `install_plugin`, `load_catalog`, `plugin_file`, `remove_skill`, `set_skill_enabled`, `skill_source`. Script environments: `Detachable`, `HandOff`, `RunningCommand`, `request_detach`, `stop_commands`, `install_python_package`, `list_python_packages`, `uninstall_python_package`, `install_node_package`, `list_node_packages`, `uninstall_node_package`, `get_interpreter_override`, `set_interpreter_override`, `working_interpreters`, `fallbacks_for_platform`, `find_pythons`, `ensure_own_python`, `own_python_offer`, `Capabilities`, `run_python_script`, `isolation_status`, `UnisolatedConversations`, `sweep_folder_grants`. The command sandbox: `sandbox_status`, `sandbox_codex`, `sandbox_home`, `record_sandbox_setup`, `sandbox_setup_argv`, `sandbox_uninstall_argv`, `warm_up_sandbox`, `expect_sandbox`, `release_private_folders`. Previews and PDF: `PresentationToolkit`, `render_single_page_preview`, `pdf_page_count`, `render_pdf_page`. The Slides panel: `read_deck`, `slide_svg`, `NotBuiltFromPages`, `deck_pdf`, `deck_html`, `deck_basic_fonts`, `deck_edits_since_coscribe`, `read_comments`, `add_comment`, `reply_to_comment`, `resolve_comment`, `delete_comment`, `CommentNotFound`, `foreign_deck`, `foreign_slide_svg`, `slide_parts`, `save_slide`, `add_slide_asset`, `insert_slide`, `duplicate_slide`, `move_slide`, `delete_slide`, `deck_history`, `step_history`, `design_system`, `deck_files`, `move_deck`, `copy_deck`, `SlideChanged`, `SectionError`, `DeckChangedError`. The Docs editor: `read_doc_blocks`, `read_doc_image`, `save_doc_blocks`, `DocChanged`, `BlocksRefused`, `remember_before_edit`, `edits_since_told`, `remember_version`, `restore_version`, `VersionGone`. Import these from `coscribe.tools`, not a submodule.

## Depends on
`coscribe.needs_permission`, `coscribe.runtime`, `coscribe.workflow_spec`.

## Invariants
- `tools` never imports LangChain or LangGraph (import contract); only `runtime` and above know
  the agent loop. Tool schemas come from function signatures and are fixed
  (`docs/decisions/0001-fixed-cached-tool-list.md`, `tests/test_tool_schema_snapshot.py`).
- Every tool carries a risk tier (`READ`, `WRITE_LOCAL`, `EXEC`, `EXTERNAL`) through
  `tool_metadata`; approvals read it.
- File tools are scoped to the workspace root; the folder guard in `exec/script_guard.py` is not a
  security boundary (`docs/decisions/0002-approvals-not-a-sandbox.md`).
- A secret's value never reaches the model: `web/http_request.py` substitutes it after the call.
- Office files are compiled from whole-page sources
  (`docs/decisions/0010-office-files-are-compiled-from-whole-page-sources.md`).

## Tests
`tests/test_*_tool.py` and `tests/test_*_tools.py` per tool, the `tests/test_*_golden.py` files and
`tests/test_tool_schema_snapshot.py`; each sub-component's card lists its own.

## Verify by hand
The file tools and conversions on Windows; connector permissions after a sign-in.

## Reviewed
2026-10-14 (first card).
