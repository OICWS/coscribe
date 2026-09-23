/**
 * The exact wire contract coscribe-web speaks, as of this session's
 * full re-read of coscribe/web/app.py, web/session.py,
 * and runtime_lg/messages.py -- not reverse-
 * engineered from the old app.js, which could itself have drifted from
 * the real backend. This file is the single source of truth the rest of
 * the frontend is built against; the backend does not change as part of
 * this rewrite, so a future contract change should show up here first
 * (and fail `tsc` everywhere it's used) rather than as a silent runtime
 * mismatch.
 */

// ---------------------------------------------------------------------
// Server -> client WebSocket events (18 types)
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
  /** Only on the state sent right after connecting: whether a turn is
   * already running on this thread (typically a scheduled run executing
   * in the background), so the page can show it as running. */
  turn_in_flight?: boolean;
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
  | { kind: "user"; text: string; images?: string[] }
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
  /** Cheap (O(1)) proxy for "does this thread have anything for
   * load_older_messages to find" -- true iff the current checkpoint's
   * own first message is a compaction summary note (see session.py's
   * send_history and _COMPACT_NOTE_PREFIX). Gates whether ChatLog even
   * offers to page further back at all -- real, live-reported bug this
   * fixes: the control used to appear unconditionally, including on a
   * brand-new thread with nothing to load. */
  has_older: boolean;
}

/** Reply to a client "load_older_messages" request (no payload) -- one
 * whole pre-/compact epoch's worth of messages, revealed in one shot
 * (see ChatSessionLG.load_older_messages's own docstring for why it's
 * never a partial slice). `has_more` is a "try again" hint, not a real
 * lookahead: true whenever this call itself found a batch, even on the
 * very last one -- an empty `entries` array is the one unambiguous
 * "nothing earlier" signal. */
export interface OlderMessagesEvent {
  type: "older_messages";
  entries: HistoryEntry[];
  has_more: boolean;
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

/** A scheduled run just started on this thread; `text` is the run's
 * prompt (it never came from this tab, so there's no local echo). */
export interface ScheduledRunStartedEvent {
  type: "scheduled_run_started";
  text: string;
}

/** create_scheduled_task's arguments as the model drafted them -- nothing
 * is saved until the user reviews it; answered with question_response. */
export interface TaskDraft {
  name?: string;
  kind?: string;
  at?: string;
  prompt?: string;
  weekday?: number | null;
  day_of_month?: number | null;
  start_date?: string | null;
  model?: string | null;
  approval_mode?: string;
}

export interface TaskDraftRequiredEvent {
  type: "task_draft_required";
  id: string;
  draft: TaskDraft;
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
 * the final answer to a turn. Also used for /saveskill's curator
 * questions/previews (session.py). */
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
}

/** Confirms a rewind_message truncation actually happened -- the frontend
 * already updated its own log optimistically (same posture edit_message's
 * own local_edit_message reducer case takes), so this is mostly a
 * consistency check, not something the UI blocks on. */
export interface RewoundEvent {
  type: "rewound";
  index: number;
}

/** /saveskill's own success confirmation. `slug` is the skill's own
 * directory name -- also the /<slug> force-load token
 * (see QuestionRequiredEvent-adjacent skills_by_slug in web/session.py). */
export interface SkillSavedEvent {
  type: "skill_saved";
  name: string;
  slug: string;
}

export type WsServerEvent =
  | StateEvent
  | HistoryEvent
  | OlderMessagesEvent
  | AgentDeltaEvent
  | ToolResultEvent
  | ApprovalRequiredEvent
  | QuestionRequiredEvent
  | ScheduledRunStartedEvent
  | TaskDraftRequiredEvent
  | UsageEvent
  | TasksChangedEvent
  | ErrorEvent
  | AgentMessageEvent
  | CompactedEvent
  | ClearedEvent
  | RewoundEvent
  | SkillSavedEvent;

// ---------------------------------------------------------------------
// Client -> server WebSocket messages (7 types)
// ---------------------------------------------------------------------

/** Slash commands (/plan, /accept-edits, /compact, /clear,
 * /saveworkflow <name>, /saveskill <name>, /init, /<skill-slug>) are NOT
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

/** Undo the last question and its reply -- distinct from edit_message,
 * which truncates *and* immediately reruns with new text. This only
 * truncates; the client hands the original question text back to the
 * composer itself rather than sending it. Same 0-based indexing as
 * EditMessageOut. Real, live-reported bug this exists to fix: the "Rewind"
 * button used to send edit_message with the turn's own unedited text,
 * which is retry (same question, new answer), not rewind. */
export interface RewindMessageOut {
  type: "rewind_message";
  index: number;
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

/** No payload -- reveals one whole pre-/compact epoch's worth of older
 * messages, prepended above whatever's currently shown. Responds with an
 * OlderMessagesEvent. See ChatSessionLG.load_older_messages's own
 * docstring for the pagination mechanism. */
export interface LoadOlderMessagesOut {
  type: "load_older_messages";
}

export type WsClientMessage =
  | UserMessageOut
  | EditMessageOut
  | RewindMessageOut
  | ApprovalResponseOut
  | QuestionResponseOut
  | StopOut
  | SwitchModelOut
  | SelectSkillsOut
  | SelectWorkspaceOut
  | LoadOlderMessagesOut;
