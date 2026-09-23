import type { TaskDraft, WsServerEvent } from "../types/wire";

/** The answer the frontend sends for a saved draft starts with this --
 * also how a replayed create_scheduled_task call tells "saved" apart
 * from "dismissed". */
export const TASK_DRAFT_SAVED_PREFIX = "The user reviewed the draft and saved it";

export type LogItem =
  // turnIndex: 0-based count among this thread's "user" items only (its
  // position ignoring every agent/tool/system item in between) -- exactly
  // the index EditMessageOut expects, since the backend counts the same
  // way over its own checkpointed HumanMessages (see web/session.py's
  // handle_edit_message). Assigned once at creation (below), not derived
  // at render time, so it stays stable even as later items are appended.
  | { id: string; kind: "user"; text: string; turnIndex: number; images?: string[] }
  | { id: string; kind: "agent"; text: string; streaming: boolean }
  | {
      id: string;
      kind: "tool";
      toolName: string;
      arguments: Record<string, unknown>;
      result?: unknown;
      /** True iff the call errored (ToolMessage.status == "error" --
       * see wire.ts's ToolResultEvent/HistoryEntry). Absent, not false,
       * until a result actually arrives -- same "no data yet" convention
       * `result` itself uses. */
      isError?: boolean;
    }
  | {
      id: string;
      kind: "approval";
      toolName: string;
      arguments: Record<string, unknown>;
      status: "pending" | "approved" | "denied";
      /** See ApprovalRequiredEvent's own doc in wire.ts -- absent (not
       * just null) whenever a checkpoint-replayed history item reconstructs
       * this LogItem shape without ever having received the live WS
       * event (these are never persisted, only ever sent live). */
      beforePreview?: string | null;
      afterPreview?: string | null;
      /** Set once the approved call actually executes -- the
       * "tool_result" reducer case merges it onto this same item rather
       * than pushing a separate "tool" one (see that case's own
       * comment). Never set for a denied call, which never runs. */
      result?: unknown;
      /** Same is_error signal as the "tool" variant's own, set alongside
       * `result` once the approved call actually executes. */
      isError?: boolean;
    }
  | {
      id: string;
      kind: "question";
      question: string;
      header: string;
      options: string[];
      multiSelect: boolean;
      status: "pending" | "answered";
      answer?: string;
    }
  | {
      id: string;
      kind: "task_draft";
      draft: TaskDraft;
      status: "pending" | "saved" | "dismissed";
      /** The name it was actually saved under -- the user may have
       * renamed it while reviewing. */
      savedName?: string;
    }
  | { id: string; kind: "system"; text: string };

export interface ChatState {
  planMode: boolean;
  acceptEdits: boolean;
  model: string;
  contextWindow: number;
  /** Skills currently spliced into this thread's instructions -- freely
   * re-toggleable any number of times (see SelectSkillsOut/the Skills
   * settings tab). */
  enabledSkills: string[];
  /** This thread's file-tool root -- see wire.ts's StateEvent.workspace_root.
   * Empty string only until the first real "state" event lands (mirrors
   * model's own empty-string-until-hydrated convention below), never null
   * after that. */
  workspaceRoot: string;
  /** See wire.ts's StateEvent.workspace_explicit -- gates whether
   * ThreadHeader's workspace badge is still clickable to pick a
   * different folder for this thread (a second pick is rejected server-
   * side once one has already succeeded). Meaningless before the first
   * real "state" event lands, same as workspaceRoot's own empty-string
   * default -- but the badge doesn't render at all until then anyway
   * (gated on workspaceRoot being non-empty), so there's nothing to get
   * wrong in the meantime. */
  workspaceExplicit: boolean;
  items: LogItem[];
  totalTokens: number;
  /** Prompt-cache stats from the most recent "usage" event, null when
   * the provider hasn't reported any yet this session (see UsageEvent's
   * own comment on why "no data" and "genuinely zero" stay distinguishable
   * all the way through). Not accumulated across turns -- mirrors
   * totalTokens' own "latest running total, not a sum" shape, and
   * Codex CLI's own per-call cache_hit_rate metric this was modeled on. */
  cacheStats: { cacheReadTokens: number; inputTokens: number; hitRate: number } | null;
  turnInFlight: boolean;
  error: string | null;
  /** Bumped on every tasks_changed (the end of each turn) -- a cheap
   * "something may have changed server-side, refetch if you care"
   * signal for REST-backed views (thread list, scheduled tasks). */
  turnTick: number;
  /** True once this connection's own "history" event has been applied.
   * Gates App.tsx's queued local sends (see pendingLocalSendsRef there):
   * a user_message dispatched optimistically *before* "history" arrives
   * gets wiped right back out the moment "history" lands, since that
   * case below replaces `items` wholesale -- a send fired the instant
   * the page paints routinely beats a real history round trip. Reset to
   * false by App.tsx on every reconnect (a fresh connection gets its own
   * fresh history event to wait for), not by this reducer, since the
   * reducer has no notion of "a new connection started." */
  historyReceived: boolean;
  /** Earlier, pre-/compact messages revealed via "load older messages" --
   * kept separate from `items` (not prepended into it) since these are
   * read-only replay (no turnIndex an edit could target -- see LogItem's
   * own "user" variant comment) and load in from the *opposite* end of
   * the log a live event ever appends to. Each successful load prepends
   * a whole earlier epoch's worth at once (see wire.ts's
   * OlderMessagesEvent), so this only ever grows further back in time,
   * never forward. */
  olderItems: LogItem[];
  /** "none" until the connect-time "history" event says has_older is
   * true (or a real /compact happens mid-connection) -- the scroll-
   * triggered auto-load in ChatLog.tsx never fires while this is "none",
   * which is what keeps it from doing anything on a brand-new thread
   * with nothing to page through (real, live-reported bug: it used to
   * offer to load more unconditionally on every thread). "loading"
   * while a request is in flight (guards against a second scroll-
   * triggered request piling on before the first resolves); "no_more"
   * once a load returned zero entries -- the auto-load stops trying
   * past that point. "idle" covers both "has_older is true but never
   * tried yet" and "tried, got something, could try again" -- see
   * OlderMessagesEvent's own has_more comment for why those two don't
   * need to be told apart client-side. */
  olderStatus: "none" | "idle" | "loading" | "no_more";
}

export const initialChatState: ChatState = {
  planMode: false,
  acceptEdits: false,
  model: "",
  contextWindow: 0,
  enabledSkills: [],
  workspaceRoot: "",
  workspaceExplicit: false,
  items: [],
  totalTokens: 0,
  cacheStats: null,
  turnInFlight: false,
  error: null,
  turnTick: 0,
  historyReceived: false,
  olderItems: [],
  olderStatus: "none",
};

/** Local, client-originated actions -- not part of the WS wire contract,
 * but handled by the same reducer since they affect the same state
 * (optimistic user bubble on send, approval-button feedback before the
 * server round-trip completes). */
export type LocalAction =
  | { type: "local_user_message"; text: string; instant: boolean; images?: string[] }
  | { type: "local_edit_message"; turnIndex: number; text: string }
  | { type: "local_rewind_message"; turnIndex: number }
  | { type: "local_approval_resolved"; id: string; approved: boolean }
  | { type: "local_question_answered"; id: string; answer: string }
  | { type: "local_task_draft_resolved"; id: string; status: "saved" | "dismissed"; savedName?: string }
  | { type: "local_connection_reset" }
  | { type: "local_switch_thread" }
  | { type: "local_request_older_messages" };

export type ChatAction = WsServerEvent | LocalAction;

let nextId = 0;
function genId(): string {
  nextId += 1;
  return `item-${nextId}`;
}

function formatDuration(elapsedMs: number): string {
  return elapsedMs < 1000 ? `${elapsedMs}ms` : `${(elapsedMs / 1000).toFixed(1)}s`;
}

function countUserItems(items: LogItem[]): number {
  return items.reduce((n, item) => n + (item.kind === "user" ? 1 : 0), 0);
}

/** Finalizes a still-streaming agent bubble left behind mid-turn -- e.g.
 * narration a model emits right before a tool call (real, live-hit:
 * DeepSeek/GLM both do this; Gemini/Anthropic mostly don't, which is why
 * this went unnoticed for a while). Only "agent_delta"/"agent_message"
 * ever mark a bubble streaming: false -- but "agent_message" only looks
 * at the *last* item, and once a "tool_result"/"approval_required"/
 * "question_required" event takes over that slot, the original bubble
 * can never be found again. Without this, that bubble's cursor never
 * clears, and the backend session.py fix (see its own _stream_turn/
 * _resolve_pending_approvals docstrings) that stops re-sending its text
 * as part of a later "agent_message" only handles the *duplication* half
 * of the bug, not this stuck-cursor half. Call this immediately before
 * pushing any item that isn't itself continuing that streaming bubble. */
function closeStreamingBubble(items: LogItem[]): LogItem[] {
  const last = items[items.length - 1];
  if (!last || last.kind !== "agent" || !last.streaming) return items;
  const closed = [...items];
  closed[closed.length - 1] = { ...last, streaming: false };
  return closed;
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "local_connection_reset":
      // A fresh connection means a fresh pagination cursor server-side
      // too (ChatSessionLG.load_older_messages' own cursor lives on the
      // session object, not across reconnects) -- carrying the previous
      // connection's olderItems/olderStatus over would either show a
      // stale "no_more" for a thread switch that never even tried, or
      // (worse) silently duplicate a batch already revealed once the
      // new connection's cursor starts over from its own current state.
      return { ...state, historyReceived: false, olderItems: [], olderStatus: "none" };

    case "local_switch_thread":
      // model/workspace stay on screen until the new thread's own "state"
      // event replaces them, instead of flashing blank in between.
      return {
        ...initialChatState,
        model: state.model,
        contextWindow: state.contextWindow,
        enabledSkills: state.enabledSkills,
        workspaceRoot: state.workspaceRoot,
        workspaceExplicit: state.workspaceExplicit,
        turnTick: state.turnTick,
      };

    case "local_request_older_messages":
      return state.olderStatus === "loading" ? state : { ...state, olderStatus: "loading" };

    case "local_user_message":
      return {
        ...state,
        // Instant commands (/plan, /clear, ...) get a near-immediate
        // "state"/"cleared"/... reply and never run an agent turn, so
        // there's nothing for a Stop button to interrupt -- mirrors
        // app.js's own INSTANT_COMMANDS guard around setTurnInFlight.
        turnInFlight: action.instant ? state.turnInFlight : true,
        error: null,
        items: [
          ...state.items,
          {
            id: genId(),
            kind: "user",
            text: action.text,
            turnIndex: countUserItems(state.items),
            images: action.images,
          },
        ],
      };

    case "local_edit_message": {
      // Optimistically truncates the log at (and including) the edited
      // turn, then appends the new text as a fresh "user" item with the
      // same turnIndex -- mirrors what the backend does to its own
      // checkpointed history (see EditMessageOut's docstring in wire.ts),
      // so the two stay in sync without waiting for a round trip. If the
      // edit is rejected server-side (out-of-range index, pending
      // approval), the "error" case below surfaces it same as any other
      // failed turn -- this optimistic truncation isn't rolled back, the
      // same posture local_user_message already takes.
      const cutIndex = state.items.findIndex(
        (item) => item.kind === "user" && item.turnIndex === action.turnIndex,
      );
      const kept = cutIndex === -1 ? state.items : state.items.slice(0, cutIndex);
      return {
        ...state,
        turnInFlight: true,
        error: null,
        items: [...kept, { id: genId(), kind: "user", text: action.text, turnIndex: action.turnIndex }],
      };
    }

    case "local_rewind_message": {
      // Unlike local_edit_message, appends nothing back -- rewind is a
      // pure undo. The turn's own question text goes to the composer
      // instead (App.tsx's onRewindMessage, via setPendingComposerText),
      // not back into the log, and no turn is running afterward.
      const cutIndex = state.items.findIndex(
        (item) => item.kind === "user" && item.turnIndex === action.turnIndex,
      );
      return cutIndex === -1 ? state : { ...state, items: state.items.slice(0, cutIndex) };
    }


    case "local_approval_resolved":
      return {
        ...state,
        items: state.items.map((item) =>
          item.kind === "approval" && item.id === action.id
            ? { ...item, status: action.approved ? "approved" : "denied" }
            : item,
        ),
      };

    case "local_question_answered":
      return {
        ...state,
        items: state.items.map((item) =>
          item.kind === "question" && item.id === action.id
            ? { ...item, status: "answered", answer: action.answer }
            : item,
        ),
      };

    case "local_task_draft_resolved":
      return {
        ...state,
        items: state.items.map((item) =>
          item.kind === "task_draft" && item.id === action.id
            ? { ...item, status: action.status, savedName: action.savedName }
            : item,
        ),
      };

    case "state":
      return {
        ...state,
        turnInFlight: action.turn_in_flight ?? state.turnInFlight,
        planMode: action.plan_mode,
        acceptEdits: action.accept_edits,
        model: action.model,
        contextWindow: action.context_window,
        enabledSkills: action.enabled_skills,
        workspaceRoot: action.workspace_root,
        workspaceExplicit: action.workspace_explicit,
      };

    case "history": {
      let userTurnIndex = 0;
      return {
        ...state,
        historyReceived: true,
        olderStatus: action.has_older ? "idle" : "none",
        items: action.entries.map((entry) => {
          if (entry.kind === "user") {
            // entry.images, when present, is the same data-URL list the
            // composer originally attached -- real, user-reported bug
            // this closes: the backend's own history entries used to
            // never carry them back out (see runtime_lg/messages.py's
            // serialize_history_for_ws_lg), so a sent image's thumbnail
            // only survived for the rest of *that* live session, not a
            // reload/reconnect/thread-switch-and-back.
            return {
              id: genId(),
              kind: "user",
              text: entry.text,
              turnIndex: userTurnIndex++,
              images: entry.images,
            } as const;
          }
          if (entry.kind === "agent") {
            return { id: genId(), kind: "agent", text: entry.text, streaming: false } as const;
          }
          if (entry.tool_name === "create_scheduled_task") {
            const saved = typeof entry.result === "string" && entry.result.startsWith(TASK_DRAFT_SAVED_PREFIX);
            return {
              id: genId(),
              kind: "task_draft",
              draft: entry.arguments as TaskDraft,
              status: saved ? "saved" : "dismissed",
            } as const;
          }
          return {
            id: genId(),
            kind: "tool",
            toolName: entry.tool_name,
            arguments: entry.arguments,
            result: entry.result,
            isError: entry.is_error,
          } as const;
        }),
      };
    }

    case "older_messages": {
      if (action.entries.length === 0) {
        return { ...state, olderStatus: "no_more" };
      }
      // turnIndex: -1 -- these are read-only replay, never rendered with
      // onEditMessage wired (see ChatLog.tsx's older-history section), so
      // the value is inert; -1 rather than a real count just makes that
      // "not a real turn index" explicit rather than accidentally
      // plausible-looking.
      const revealed: LogItem[] = action.entries.map((entry) => {
        if (entry.kind === "user") {
          return { id: genId(), kind: "user", text: entry.text, turnIndex: -1, images: entry.images } as const;
        }
        if (entry.kind === "agent") {
          return { id: genId(), kind: "agent", text: entry.text, streaming: false } as const;
        }
        return {
          id: genId(),
          kind: "tool",
          toolName: entry.tool_name,
          arguments: entry.arguments,
          result: entry.result,
          isError: entry.is_error,
        } as const;
      });
      return {
        ...state,
        olderItems: [...revealed, ...state.olderItems],
        olderStatus: action.has_more ? "idle" : "no_more",
      };
    }

    case "agent_delta": {
      const items = [...state.items];
      const last = items[items.length - 1];
      if (last && last.kind === "agent" && last.streaming) {
        items[items.length - 1] = { ...last, text: last.text + action.text };
      } else {
        items.push({ id: genId(), kind: "agent", text: action.text, streaming: true });
      }
      return { ...state, items };
    }

    case "agent_message": {
      const items = [...state.items];
      const last = items[items.length - 1];
      if (last && last.kind === "agent" && last.streaming) {
        items[items.length - 1] = { ...last, text: action.text, streaming: false };
      } else {
        items.push({ id: genId(), kind: "agent", text: action.text, streaming: false });
      }
      return { ...state, items, turnInFlight: false };
    }

    case "tool_result": {
      // Copied again after closeStreamingBubble, which returns state.items
      // itself unchanged when there's nothing to close -- items[i] = ...
      // below must never end up mutating that shared reference in place.
      const items = [...closeStreamingBubble(state.items)];
      for (let i = items.length - 1; i >= 0; i -= 1) {
        const item = items[i];
        if (item.kind === "tool" && item.toolName === action.tool_name && item.result === undefined) {
          items[i] = {
            ...item,
            arguments: action.arguments,
            result: action.result,
            isError: action.is_error,
          };
          return { ...state, items };
        }
        // A gated call only ever produced an "approval" LogItem live --
        // there's no separate "tool call started" WS event to have
        // created a "tool" placeholder for the branch above to find (see
        // wire.ts's ToolResultEvent, the only tool-shaped message a live
        // turn sends). Once approved and actually executed, its result
        // belongs on *this same* item (kept as "approval", not
        // recreated as "tool" -- that would drop its status/
        // beforePreview/afterPreview, and ApprovalDetail still wants to
        // show "Approved" alongside the result, not replace it). Merging
        // here, instead of leaving the tool_result to fall through to
        // the push below, is what fixes a real bug found live: pushing
        // a *second* item for the same action left both it and the
        // still-present approval item describing the same edit, so a
        // collapsed tool-run group summarized both -- "Wrote notes.txt,
        // Wrote notes.txt" for one write_file call.
        if (item.kind === "approval" && item.toolName === action.tool_name && item.status === "approved") {
          items[i] = {
            ...item,
            arguments: action.arguments,
            result: action.result,
            isError: action.is_error,
          };
          return { ...state, items };
        }
      }
      items.push({
        id: genId(),
        kind: "tool",
        toolName: action.tool_name,
        arguments: action.arguments,
        result: action.result,
        isError: action.is_error,
      });
      return { ...state, items };
    }

    case "approval_required":
      return {
        ...state,
        items: [
          ...closeStreamingBubble(state.items),
          {
            id: action.id,
            kind: "approval",
            toolName: action.tool_name,
            arguments: action.arguments,
            status: "pending",
            beforePreview: action.before_preview,
            afterPreview: action.after_preview,
          },
        ],
      };

    case "task_draft_required":
      return {
        ...state,
        items: [
          ...closeStreamingBubble(state.items),
          { id: action.id, kind: "task_draft", draft: action.draft, status: "pending" },
        ],
      };

    case "scheduled_run_started":
      return {
        ...state,
        turnInFlight: true,
        items: [
          ...state.items,
          { id: genId(), kind: "user", text: action.text, turnIndex: countUserItems(state.items) },
        ],
      };

    case "question_required":
      return {
        ...state,
        items: [
          ...closeStreamingBubble(state.items),
          {
            id: action.id,
            kind: "question",
            question: action.question,
            header: action.header,
            options: action.options,
            multiSelect: action.multi_select,
            status: "pending",
          },
        ],
      };

    case "usage":
      return {
        ...state,
        totalTokens: action.total_tokens,
        cacheStats:
          action.cache_read_tokens !== undefined &&
          action.input_tokens !== undefined &&
          action.cache_hit_rate !== undefined
            ? {
                cacheReadTokens: action.cache_read_tokens,
                inputTokens: action.input_tokens,
                hitRate: action.cache_hit_rate,
              }
            : state.cacheStats,
      };

    case "error":
      return {
        ...state,
        error: action.message,
        turnInFlight: false,
        items: closeStreamingBubble(state.items),
      };

    case "compacted":
      return {
        ...state,
        turnInFlight: false,
        items: [
          ...state.items,
          {
            id: genId(),
            kind: "system",
            text: `Compacted ${action.before} messages down to ${action.after} · ${formatDuration(action.elapsed_ms)}.`,
          },
        ],
      };

    case "cleared":
      return {
        ...state,
        turnInFlight: false,
        items: [
          {
            id: genId(),
            kind: "system",
            text: "Cleared this thread's conversation history.",
          },
        ],
      };

    case "rewound":
      // No-op: local_rewind_message already truncated items optimistically
      // (see App.tsx's onRewindMessage) -- this is just the server's own
      // confirmation that the checkpointed history actually matches.
      return state;

    case "skill_saved":
      return {
        ...state,
        turnInFlight: false,
        items: [
          ...state.items,
          {
            id: genId(),
            kind: "system",
            text: `Saved skill "${action.name}" -- try /${action.slug} to use it directly.`,
          },
        ],
      };

    case "tasks_changed":
      return { ...state, turnInFlight: false, turnTick: state.turnTick + 1 };

    default: {
      const _exhaustive: never = action;
      return _exhaustive;
    }
  }
}
