/**
 * The exact wire contract coscribe-web speaks, as of this session's
 * full re-read of coscribe/web/app.py, web/session.py,
 * runtime_lg/messages.py, and tools/workflows.py -- not reverse-
 * engineered from the old app.js, which could itself have drifted from
 * the real backend. This file is the single source of truth the rest of
 * the frontend is built against; the backend does not change as part of
 * this rewrite, so a future contract change should show up here first
 * (and fail `tsc` everywhere it's used) rather than as a silent runtime
 * mismatch.
 */

// ---------------------------------------------------------------------
// Server -> client WebSocket events (17 types)
// ---------------------------------------------------------------------

export interface StateEvent {
  type: "state";
  plan_mode: boolean;
  accept_edits: boolean;
  model: string;
  context_window: number;
  /** Which built-in/local skills are currently spliced into this thread's
   * instructions -- freely re-toggleable any number of times per thread
   * (see SelectSkillsOut). */
  enabled_skills: string[];
  /** This thread's file-tool root -- never null: an unset thread falls
   * back to the server's global default (Settings > Workspace's "default
   * for new sessions" value), so there's always a real path to show.
   * Fixed once per thread (see SelectWorkspaceOut), not freely
   * re-toggleable like enabled_skills. */
  workspace_root: string;
  /** True iff workspace_root above came from a real per-thread choice
   * (a SelectWorkspaceOut that already succeeded), false if it's still
   * just settings.workspace_root's own global default -- the frontend's
   * only way to tell those apart (workspace_root itself can't: a user
   * can deliberately pick the same path the default already points at).
   * Gates whether the workspace badge is still clickable to change it --
   * see ThreadHeader.tsx -- since a second SelectWorkspaceOut on a
   * thread that already has one is rejected server-side. */
  workspace_explicit: boolean;
}

export type HistoryEntry =
  | { kind: "user"; text: string }
  | { kind: "agent"; text: string }
  | {
      kind: "tool";
      tool_name: string;
      arguments: Record<string, unknown>;
      result: unknown;
      /** Off the checkpointed ToolMessage's own `.status` -- true iff the
       * tool's Python body raised (see agent.py's
       * _CatchToolErrorsMiddleware). Same field ToolResultEvent carries
       * for a live call, below. */
      is_error: boolean;
    };

export interface HistoryEvent {
  type: "history";
  entries: HistoryEntry[];
}

export interface AgentDeltaEvent {
  type: "agent_delta";
  text: string;
}

export interface ToolResultEvent {
  type: "tool_result";
  tool_name: string;
  /** The call's own arguments, recovered server-side from the preceding
   * AIMessage's tool_calls (see web/session.py's _stream_turn) -- lets a
   * live turn's collapsed row show the real filename/query the way
   * history replay already can from its own checkpointed arguments,
   * instead of always falling back to generic phrasing ("a file"). */
  arguments: Record<string, unknown>;
  result: unknown;
  /** True iff the tool's Python body raised (LangChain's ToolMessage.status
   * == "error", set by agent.py's _CatchToolErrorsMiddleware) -- the one
   * generic, works-for-every-tool failure signal, not inferred from
   * `result`'s own shape. Drives the transcript's red failure styling and
   * "(N failed)" counts (see transcriptGrouping.ts). */
  is_error: boolean;
}

export interface ApprovalRequiredEvent {
  type: "approval_required";
  id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  /** Bare filenames under GET /api/previews/, same convention as a
   * completed tool result's own preview_path (see ChatLog.tsx's
   * previewPathOf) -- a before/after render of the one pptx edit this
   * approval is gating, from web/session.py's _build_pptx_edit_preview.
   * Both null whenever that doesn't apply (not a previewable pptx edit,
   * LibreOffice/poppler missing, or the dry run itself failed) -- never
   * an error, just nothing to show beside the raw arguments below. */
  before_preview: string | null;
  after_preview: string | null;
}

/** ask_user_question's own interrupt -- unlike approval_required, there's
 * nothing to approve/reject, only an answer to send back (see
 * QuestionResponseOut). `options` is already parsed into an array
 * server-side (session.py splits the tool's newline-separated `options`
 * string and drops blank lines) -- never empty when this event fires,
 * but the client should still let the user type a free-text answer
 * instead of only offering the listed options. */
export interface QuestionRequiredEvent {
  type: "question_required";
  id: string;
  question: string;
  header: string;
  options: string[];
  multi_select: boolean;
}

export interface UsageEvent {
  type: "usage";
  total_tokens: number;
  /** Present only when the provider actually reported prompt-cache
   * stats for this call (Anthropic's explicit cache_control breakpoints,
   * or an OpenAI-compatible provider's own implicit caching -- GLM
   * documents this, no client-side opt-in needed). Absent, not zero,
   * when nothing was reported -- "no cache data" and "cache genuinely
   * missed" read very differently to someone checking whether caching
   * is working at all. */
  cache_read_tokens?: number;
  input_tokens?: number;
  cache_hit_rate?: number;
}

export interface TasksChangedEvent {
  type: "tasks_changed";
}

export interface ErrorEvent {
  type: "error";
  message: string;
}

/** A generic "chat bubble from the assistant" event -- not exclusively
 * the final answer to a turn. Also used for workflow save-curator
 * questions/previews and clarification-flow prompts (session.py). */
export interface AgentMessageEvent {
  type: "agent_message";
  text: string;
}

export interface CompactedEvent {
  type: "compacted";
  before: number;
  after: number;
  elapsed_ms: number;
}

export interface ClearedEvent {
  type: "cleared";
  cancelled_recording: boolean;
}

export interface RecordingStartedEvent {
  type: "recording_started";
  discarded_previous: boolean;
}

/** step_count is present only for mode:"chain" -- absent (not zero, not
 * null) for mode:"agent", matching session.py's own dict construction. */
export interface WorkflowSavedEvent {
  type: "workflow_saved";
  name: string;
  mode: "chain" | "agent";
  step_count?: number;
}

export type WorkflowRunStepStatus = "pending" | "running" | "done" | "failed" | "stopped";

export interface WorkflowRunStep {
  index: number;
  tool_name: string;
  status: WorkflowRunStepStatus;
  detail: string | null;
}

export type WorkflowRunStatus = "running" | "completed" | "failed" | "stopped";

export interface WorkflowRun {
  run_id: string;
  workflow_name: string;
  mode: "chain" | "agent";
  status: WorkflowRunStatus;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  /** Always [] for mode:"agent" runs -- no per-step progress is tracked
   * for agent-mode workflows (session.py's deliberate simplification). */
  steps: WorkflowRunStep[];
}

export interface WorkflowRunProgressEvent {
  type: "workflow_run_progress";
  run: WorkflowRun;
}

export interface WorkflowRunStartedEvent {
  type: "workflow_run_started";
  name: string;
}

/** /saveskill's own success confirmation -- the reusable-knowledge
 * counterpart to WorkflowSavedEvent above (see runtime_lg/
 * skill_authoring.py's module docstring for why /saveskill is a
 * separate flow from /saveworkflow, not a third mode of it). `slug` is
 * the skill's own directory name -- also the /<slug> force-load token
 * (see QuestionRequiredEvent-adjacent skills_by_slug in web/session.py). */
export interface SkillSavedEvent {
  type: "skill_saved";
  name: string;
  slug: string;
}

export type WsServerEvent =
  | StateEvent
  | HistoryEvent
  | AgentDeltaEvent
  | ToolResultEvent
  | ApprovalRequiredEvent
  | QuestionRequiredEvent
  | UsageEvent
  | TasksChangedEvent
  | ErrorEvent
  | AgentMessageEvent
  | CompactedEvent
  | ClearedEvent
  | RecordingStartedEvent
  | WorkflowSavedEvent
  | SkillSavedEvent
  | WorkflowRunProgressEvent
  | WorkflowRunStartedEvent;

// ---------------------------------------------------------------------
// Client -> server WebSocket messages (6 types)
// ---------------------------------------------------------------------

/** Slash commands (/plan, /accept-edits, /compact, /clear,
 * /startworkflow, /endworkflow <name>, /saveworkflow <name>,
 * /saveskill <name>, /runworkflow <name>, /init, /<skill-slug>) are NOT
 * separate message types -- they're plain `text` here, parsed server-
 * side (session.py's _handle_user_message_locked). `images` are
 * multimodal image_url content parts sent as base64 data URLs, not
 * uploaded files (those go through POST /api/upload instead, see
 * rest.ts). */
export interface UserMessageOut {
  type: "user_message";
  text: string;
  images?: string[];
}

/** Edit an earlier user turn and regenerate from there. `index` is
 * 0-based, counting only "user"-kind LogItems -- lines up exactly with
 * HistoryEvent's own entries (every checkpointed HumanMessage maps 1:1
 * to one "user" entry there), so no separate id needs tracking. The
 * backend truncates its checkpointed history back to that turn and reruns
 * `text` through the normal turn path, so the same events a fresh
 * user_message would produce (agent_delta/agent_message/tool_result/...)
 * follow this one too -- there is no dedicated "edited" confirmation
 * event. */
export interface EditMessageOut {
  type: "edit_message";
  index: number;
  text: string;
  images?: string[];
}

export interface ApprovalResponseOut {
  type: "approval_response";
  id: string;
  approved: boolean;
}

/** `answer` is always plain text -- for a multi_select question, the
 * client joins the chosen options into one string (comma-separated)
 * before sending; the server never parses it back apart, it's delivered
 * to the model as-is. */
export interface QuestionResponseOut {
  type: "question_response";
  id: string;
  answer: string;
}

/** Cooperative stop -- also immediately denies any pending approval so a
 * stop isn't blocked behind an unanswered approval prompt. Distinct from
 * a `/stop` typed as user_message text: the current frontend
 * special-cases `/stop` to send this instead, since only this type can
 * interrupt an already-running turn. */
export interface StopOut {
  type: "stop";
}

/** `model` must contain ":" (e.g. "anthropic:claude-opus-4-6"). Directly
 * awaited server-side; responds with a `state` event on success or
 * `error` on failure. */
export interface SwitchModelOut {
  type: "switch_model";
  model: string;
}

/** Callable any number of times per thread -- every message fully
 * replaces the enabled set (not additive/subtractive), so send the
 * complete next set of skill names, not just the one being toggled.
 * Responds with a `state` event reflecting the new enabled_skills.
 * Unknown names are dropped silently server-side, not rejected. */
export interface SelectSkillsOut {
  type: "select_skills";
  skills: string[];
}

/** Only valid once per thread -- a thread that already has a workspace
 * (either explicitly chosen or via ?workspace= at connect time) rejects
 * a second call with an `error` event. Responds with a `state` event
 * reflecting the new workspace_root on success. */
export interface SelectWorkspaceOut {
  type: "select_workspace";
  path: string;
}

export type WsClientMessage =
  | UserMessageOut
  | EditMessageOut
  | ApprovalResponseOut
  | QuestionResponseOut
  | StopOut
  | SwitchModelOut
  | SelectSkillsOut
  | SelectWorkspaceOut;
