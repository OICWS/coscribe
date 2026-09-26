import { createContext, lazy, Suspense, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  groupHasPendingApproval,
  groupToolRuns,
  groupTurns,
  latestWorkflowDraftId,
  isRunning,
  summarizeGroupParts,
  summarizeItemParts,
  type SummaryParts,
  type ToolRunGroup,
  type Turn,
  type WorkflowDraftEntry,
} from "../lib/transcriptGrouping";
import type { LogItem } from "../state/reducer";
import { CopyButton } from "./CopyButton";
import { EmptyState } from "./EmptyState";
import { ChevronRightIcon, PencilIcon, RetryIcon, RewindIcon } from "./icons";
import { ImageLightbox } from "./ImageLightbox";
import { type PptxShapeCapture, PptxShapeOverlay } from "./PptxShapeOverlay";
import { QuestionCard } from "./QuestionCard";
import { RUN_PROMPT_PREFIX, ScheduledRunCard } from "./ScheduledRunCard";
import { TaskDraftCard } from "./TaskDraftCard";
import { WorkflowDraftCard } from "./workflow/WorkflowDraftCard";

/** react-markdown + remark/rehype + katex is the single biggest dependency
 * added to this app (roughly triples the production bundle) -- code-split
 * it into its own chunk so a session that never renders a markdown-heavy
 * reply doesn't pay for it on first load. The Suspense fallback is the
 * plain, unparsed text (what the agent bubble looked like before this
 * feature existed), so the one-time chunk fetch shows real text, not a
 * spinner or blank bubble. */
const Markdown = lazy(() => import("./Markdown").then((m) => ({ default: m.Markdown })));

/** write_docx/write_xlsx/write_pptx's tool result carries a `preview_path`
 * (a bare filename under GET /api/previews/) when LibreOffice rendered a
 * thumbnail -- other tools' results never have this shape. */
function previewPathOf(result: unknown): string | null {
  if (!result || typeof result !== "object" || !("preview_path" in result)) return null;
  const value = (result as Record<string, unknown>).preview_path;
  return typeof value === "string" ? value : null;
}

/** Click-a-shape-to-target-it (PptxShapeOverlay) only makes sense for a
 * .pptx preview -- write_docx/write_xlsx thumbnails share the exact same
 * preview_path shape but have no shape_index concept to click into.
 * `arguments.slide` covers every edit tool that targets one slide
 * (edit_pptx_shape, delete/duplicate/reorder_pptx_slide, ...); a bare
 * write_pptx has no `slide` argument at all, and its own preview is
 * always the deck's first slide (see tools/_thumbnail.py). */
function pptxOverlayTargetOf(arguments_: Record<string, unknown>): { path: string; slide: number } | null {
  const path = arguments_.path;
  if (typeof path !== "string" || !path.toLowerCase().endsWith(".pptx")) return null;
  const slide = arguments_.slide;
  return { path, slide: typeof slide === "number" ? slide : 1 };
}

type ToolOrApprovalItem = Extract<LogItem, { kind: "tool" | "approval" }>;
type TaskDraftItem = Extract<LogItem, { kind: "task_draft" }>;

interface ChatLogProps {
  items: LogItem[];
  /** A reply is being generated -- its tool calls without a result yet
   * are shown as running. */
  turnInFlight?: boolean;
  onApprove: (id: string, approved: boolean) => void;
  onAnswerQuestion: (id: string, answer: string) => void;
  /** Undefined while a turn is in flight -- editing mid-turn would race
   * the very history the edit is about to truncate, so the affordance is
   * hidden entirely rather than left clickable-but-erroring. */
  onEditMessage?: (turnIndex: number, text: string) => void;
  /** Rewind (undo the last question+reply, hand the text back to the
   * composer) -- same "hidden while a turn is in flight" reasoning as
   * onEditMessage above, and offered on the same footer, see TurnView. */
  onRewindMessage?: (turnIndex: number, text: string) => void;
  /** True until this thread's history has arrived -- an existing
   * thread shows a spinner then, not the brand-new-thread greeting. */
  loading: boolean;
  onReviewTaskDraft: (item: TaskDraftItem) => void;
  onDismissTaskDraft: (item: TaskDraftItem) => void;
  onReviewWorkflowDraft?: (entry: WorkflowDraftEntry) => void;
  /** A shape clicked in a pptx preview (PptxShapeOverlay) -- threaded up
   * to App.tsx exactly like BrowserPanel's own onSendToChat. */
  onPptxShapePicked: (capture: PptxShapeCapture) => void;
  /** Earlier, pre-/compact messages revealed by scrolling near the top
   * of the log -- kept as a separate list rendered above `items` rather
   * than prepended into it, see ChatState.olderItems' own comment for
   * why. Always read-only (no onEditMessage wired for these -- see
   * TurnView's call below), and rendered as plain turns via the same
   * groupTurns pass `items` gets, so a pre-compact turn looks identical
   * to a live one apart from that. */
  olderItems: LogItem[];
  olderStatus: "none" | "idle" | "loading" | "no_more";
  onLoadOlder: () => void;
}

/** How close to the top of the scroll container (in px) counts as "the
 * user is looking at the oldest thing currently loaded" -- real, live-
 * reported preference: a manual "Load earlier messages" button read as
 * unnecessary friction, the user wanted plain scroll-up-to-load-more
 * like every other chat product. A small, nonzero threshold (rather
 * than exactly 0) means the load kicks in a little before the user
 * actually hits the physical top, so the prepend (and its own scroll-
 * anchoring, below) has already landed by the time they'd notice. */
const SCROLL_LOAD_OLDER_THRESHOLD_PX = 150;

export function ChatLog({
  items,
  onApprove,
  onAnswerQuestion,
  onEditMessage,
  onRewindMessage,
  loading,
  onReviewTaskDraft,
  onDismissTaskDraft,
  onReviewWorkflowDraft,
  onPptxShapePicked,
  olderItems,
  olderStatus,
  onLoadOlder,
  turnInFlight = false,
}: ChatLogProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const turns = groupTurns(items);
  const olderTurns = groupTurns(olderItems);
  const latestDraftId = latestWorkflowDraftId([...olderItems, ...items]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
    // Deliberately not [items, olderItems] -- appending a live item
    // should always jump to the bottom, but revealing older history
    // above should never move the viewport at all (see the scroll-
    // anchoring effect below, which handles that case on its own terms
    // instead of fighting this one for the same scroll position).
  }, [items]);

  // Keeps whatever the user was already looking at pinned in place when
  // a new batch of older messages is prepended above it -- without this,
  // the browser's own default scroll-anchoring is inconsistent across a
  // prepend this large (a whole turn's worth of new DOM nodes above the
  // current scrollTop), and the viewport visibly jumps. Captures height
  // *before* the prepend (in onLoadOlder's own caller, App.tsx, via the
  // ref read here at layout time -- scrollHeight already reflects the
  // old, pre-prepend DOM on this effect's first run for a given
  // olderItems change) and restores the equivalent scrollTop after.
  const prevOlderScrollHeight = useRef<number | null>(null);
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (prevOlderScrollHeight.current !== null) {
      el.scrollTop += el.scrollHeight - prevOlderScrollHeight.current;
    }
    prevOlderScrollHeight.current = el.scrollHeight;
  }, [olderItems]);

  // Auto-loads on scroll-up instead of a manual button -- a ref (not
  // onLoadOlder in the dependency array) holds the latest callback so
  // this effect only re-subscribes when olderStatus itself actually
  // changes, not on every unrelated parent re-render (App.tsx's
  // onLoadOlderMessages is a fresh closure each render); without that,
  // a render happening to land while still "idle" and still scrolled
  // near the top would fire a duplicate load before the first one's own
  // "loading" status had a chance to propagate back down and stop it.
  // Checks once immediately on entering "idle" (not just on a scroll
  // event) so a thread short enough that its content doesn't even fill
  // the viewport -- already effectively "at the top" -- still loads
  // without requiring an actual scroll gesture first.
  const onLoadOlderRef = useRef(onLoadOlder);
  onLoadOlderRef.current = onLoadOlder;
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || olderStatus !== "idle") return;
    const checkAndLoad = () => {
      if (el.scrollTop < SCROLL_LOAD_OLDER_THRESHOLD_PX) onLoadOlderRef.current();
    };
    checkAndLoad();
    el.addEventListener("scroll", checkAndLoad);
    return () => el.removeEventListener("scroll", checkAndLoad);
  }, [olderStatus]);

  if (items.length === 0) {
    return (
      <div data-testid="chat-log" className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex h-full items-center justify-center">
            <div
              className="h-6 w-6 animate-spin rounded-full border-[3px] border-[color-mix(in_srgb,var(--fg)_15%,transparent)] border-t-[var(--accent)] motion-reduce:animate-none"
              role="status"
              aria-label="Loading conversation"
            />
          </div>
        ) : (
          <EmptyState />
        )}
      </div>
    );
  }

  return (
    <div data-testid="chat-log" className="flex-1 overflow-y-auto" ref={scrollRef}>
      <div className="mx-auto flex w-full max-w-[880px] flex-col gap-3 px-4 py-4">
        {olderStatus === "loading" && (
          <p className="self-center text-xs text-[var(--muted)]">Loading earlier messages...</p>
        )}
        {olderTurns.map((turn) => (
          // No onEditMessage (read-only replay, see olderItems' own prop
          // comment) and onPptxShapePicked is a no-op -- targeting a
          // follow-up edit off a pre-compact preview isn't something
          // this pass adds a real affordance for, and TurnView requires
          // the prop regardless of whether anything's clickable.
          <TurnView
            key={turn.id}
            turn={turn}
            onApprove={onApprove}
            onAnswerQuestion={onAnswerQuestion}
            onPptxShapePicked={() => {}}
          />
        ))}
        {olderItems.length > 0 && <div className="border-b border-[var(--border)]" />}
        {turns.map((turn, i) => (
          <TurnView
            key={turn.id}
            turn={turn}
            isLastTurn={i === turns.length - 1}
            onApprove={onApprove}
            onAnswerQuestion={onAnswerQuestion}
            onEditMessage={onEditMessage}
            onRewindMessage={onRewindMessage}
            onReviewTaskDraft={onReviewTaskDraft}
            onDismissTaskDraft={onDismissTaskDraft}
            onReviewWorkflowDraft={onReviewWorkflowDraft}
            latestDraftId={latestDraftId}
            live={turnInFlight && i === turns.length - 1}
            onPptxShapePicked={onPptxShapePicked}
          />
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}

/** Renders one turn's items (via the same groupToolRuns pass the whole
 * thread used to go through directly) plus, once the turn is actually
 * done, a bottom-left copy+retry+rewind+relative-time footer -- replaces
 * the old one-CopyButton-per-agent-message placement (see ROADMAP.md's
 * Phase 8am items 7-8). "Done" means: has a final agent reply that isn't
 * still streaming, and no approval in this turn is still waiting on the
 * user -- the same real backend rule handle_edit_message/
 * handle_rewind_message both enforce ("Resolve the pending approval
 * before editing/rewinding"), so neither button is ever offered
 * somewhere it would just come back as a WS error.
 *
 * Retry and Rewind are two different operations, previously conflated
 * under one "Rewind" button that actually retried (real, live-reported
 * bug): Retry truncates the turn and immediately resubmits the same
 * question (onEditMessage with unedited text) -- same question, new
 * answer. Rewind truncates and stops there, handing the original
 * question text back to the composer instead (onRewindMessage) -- an
 * undo, with no new answer generated. Retry gets its own RetryIcon (a
 * two-arrowhead refresh cycle) rather than RewindIcon mirrored --
 * explicit correction: a single arc flipped horizontally still read as
 * "the same arrow, unclear which way" at 14px and was mistaken for
 * Rewind in real use, not a clear inverse.
 *
 * Both restricted to `isLastTurn` -- explicit correction: every past turn
 * used to offer this, but reaching back to an earlier turn while later
 * ones already exist is exactly what "edit an earlier message" (the
 * pencil icon on the user bubble itself, still available on every turn)
 * already does more explicitly; a second, identically-named affordance on
 * every turn read as "jump to any point," which isn't what either of
 * these do (both only ever discard everything *after* the turn clicked,
 * same as edit). The whole footer is also hover-only (opacity-0, revealed
 * via the turn's own group/turn on hover) instead of permanently visible
 * -- matches the rest of this app's hover-reveal convention for secondary
 * actions (ThreadRow's "..." menu, the user bubble's own edit pencil)
 * rather than a footer under every single reply, always on screen. */
function TurnView({
  turn,
  isLastTurn = false,
  onApprove,
  onAnswerQuestion,
  onEditMessage,
  onRewindMessage,
  onReviewTaskDraft,
  onDismissTaskDraft,
  onReviewWorkflowDraft,
  latestDraftId = null,
  live = false,
  onPptxShapePicked,
}: {
  turn: Turn;
  isLastTurn?: boolean;
  /** This turn is the one a reply is still being generated for. */
  live?: boolean;
  onApprove: (id: string, approved: boolean) => void;
  onAnswerQuestion: (id: string, answer: string) => void;
  onEditMessage?: (turnIndex: number, text: string) => void;
  onRewindMessage?: (turnIndex: number, text: string) => void;
  onReviewTaskDraft?: (item: TaskDraftItem) => void;
  onDismissTaskDraft?: (item: TaskDraftItem) => void;
  onReviewWorkflowDraft?: (entry: WorkflowDraftEntry) => void;
  latestDraftId?: string | null;
  onPptxShapePicked: (capture: PptxShapeCapture) => void;
}) {
  const entries = groupToolRuns(turn.items);
  const finalAgentItem = turn.finalAgentItem;
  const userItem = turn.userItem;
  const canFooter =
    finalAgentItem !== null && userItem !== null && !finalAgentItem.streaming && !turn.hasPendingApproval;

  return (
    <div className="group/turn flex flex-col gap-3">
      {/* groupToolRuns now always wraps a tool/approval run into a
       * ToolRunGroup, even a run of one (see its own comment) -- a bare
       * "tool"/"approval" LogItem can no longer reach this map at all,
       * so there's no case for it here anymore. */}
      {entries.map((entry) =>
        entry.kind === "tool_run" ? (
          <ToolRunGroupView
            key={entry.id}
            group={entry}
            live={live}
            onApprove={onApprove}
            onPptxShapePicked={onPptxShapePicked}
          />
        ) : entry.kind === "question" ? (
          <QuestionCard key={entry.id} item={entry} onAnswer={onAnswerQuestion} />
        ) : entry.kind === "task_draft" ? (
          <TaskDraftCard key={entry.id} item={entry} onReview={onReviewTaskDraft} onDismiss={onDismissTaskDraft} />
        ) : entry.kind === "workflow_draft" ? (
          <WorkflowDraftCard
            key={entry.id}
            entry={entry}
            onReview={onReviewWorkflowDraft}
            superseded={latestDraftId !== null && entry.id !== latestDraftId}
          />
        ) : (
          <LogItemView key={entry.id} item={entry} onEditMessage={isLastTurn ? onEditMessage : undefined} />
        ),
      )}
      {canFooter && (
        <div className="-mt-2 flex items-center gap-0.5 self-start opacity-0 transition-opacity group-hover/turn:opacity-100">
          <CopyButton
            getText={() => finalAgentItem.text}
            title="Copy reply"
            className="flex h-6 w-6 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
          />
          {onEditMessage && isLastTurn && (
            <button
              type="button"
              title="Retry -- ask this again"
              className="flex h-6 w-6 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
              onClick={() => onEditMessage(userItem.turnIndex, userItem.text)}
            >
              <RetryIcon className="h-3.5 w-3.5" />
            </button>
          )}
          {onRewindMessage && isLastTurn && (
            <button
              type="button"
              title="Rewind -- undo this question"
              className="flex h-6 w-6 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
              onClick={() => onRewindMessage(userItem.turnIndex, userItem.text)}
            >
              <RewindIcon className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** The Sub Agents panel leaves out the "+N -M" line counts. */
const ShowDiffStats = createContext(true);

/** A read-only transcript -- a sub-agent's, in the Sub Agents panel --
 * drawn with the chat's own components. `live` shows its last call as
 * running. */
export function TranscriptItems({
  items,
  live,
  onApprove,
}: {
  items: LogItem[];
  live: boolean;
  onApprove: (id: string, approved: boolean) => void;
}) {
  const entries = groupToolRuns(items);
  return (
    <ShowDiffStats.Provider value={false}>
      <div className="flex flex-col gap-3">
        {entries.map((entry) =>
          entry.kind === "tool_run" ? (
            <ToolRunGroupView
              key={entry.id}
              group={entry}
              live={live}
              onApprove={onApprove}
              onPptxShapePicked={() => {}}
            />
          ) : entry.kind === "agent" ? (
            <LogItemView key={entry.id} item={entry} />
          ) : null,
        )}
      </div>
    </ShowDiffStats.Provider>
  );
}

/** Clauses after the first read as one sentence ("Ran 3 commands, read a
 * file"); an acronym-led verb ("PDF ...") keeps its capitals. */
function inSentence(parts: SummaryParts, index: number): SummaryParts {
  if (index === 0 || !/^[A-Z][a-z]/.test(parts.verb)) return parts;
  return { ...parts, verb: parts.verb[0].toLowerCase() + parts.verb.slice(1) };
}

/** "verb ⟨object chip⟩". A failed call is red as a whole; an aggregated
 * clause shows only its "(N failed)" count in red. The running call's
 * object is plain text so the shimmer (clipped to the text) runs through
 * it too. */
function SummaryLabel({ parts }: { parts: SummaryParts }) {
  const showDiffStats = useContext(ShowDiffStats);
  if (parts.shimmer) {
    return (
      <span className="shimmer-text">
        {parts.verb}
        {parts.object && `${parts.glue ?? " "}${parts.object}`}
      </span>
    );
  }
  const label = (
    <>
      {parts.verb}
      {parts.object && (
        <>
          {parts.glue ?? " "}
          <code className="rounded bg-[var(--code-bg)] px-1 py-0.5 font-mono text-[0.85em]">{parts.object}</code>
        </>
      )}
      {showDiffStats && parts.diffStat && <DiffStat added={parts.diffStat.added} removed={parts.diffStat.removed} />}
      {parts.failedCount !== undefined && (
        <span className="ml-1 text-[var(--danger)]">({parts.failedCount} failed)</span>
      )}
    </>
  );
  return parts.failed ? <span className="text-[var(--danger)]">{label}</span> : label;
}

/** "+N -M", green/red: the one exception to the app's mono-red palette,
 * since diff-stat colors are a convention readers already know. */
function DiffStat({ added, removed }: { added: number; removed: number }) {
  return (
    <span className="ml-1.5 font-mono text-[0.85em] tabular-nums">
      {added > 0 && <span className="text-emerald-600 dark:text-emerald-400">+{added}</span>}
      {removed > 0 && <span className="text-[var(--danger)]">-{removed}</span>}
    </span>
  );
}

function Disclosure({ open }: { open: boolean }) {
  return (
    <ChevronRightIcon
      aria-hidden="true"
      className={`h-3.5 w-3.5 shrink-0 transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`}
    />
  );
}

/** A run of tool calls: one sentence-like summary with a chevron after
 * it; opened, a bordered list with a row per call, each opening to its
 * result. A run holding an unresolved approval is shown unwrapped
 * instead -- an action waiting on the user must never sit behind a
 * disclosure. `group.id` is stable across that change, so the open state
 * survives the approval resolving. */
function ToolRunGroupView({
  group,
  live,
  onApprove,
  onPptxShapePicked,
}: {
  group: ToolRunGroup;
  live: boolean;
  onApprove: (id: string, approved: boolean) => void;
  onPptxShapePicked: (capture: PptxShapeCapture) => void;
}) {
  const [open, setOpen] = useState(false);

  if (groupHasPendingApproval(group)) {
    return (
      <div className="flex flex-col gap-1.5 self-start">
        {group.items.map((item) => (
          <ToolCallRow key={item.id} item={item} onApprove={onApprove} onPptxShapePicked={onPptxShapePicked} />
        ))}
      </div>
    );
  }

  const header = summarizeGroupParts(group.items, live);
  const single = group.items.length === 1 ? group.items[0] : null;
  return (
    <div className="w-full max-w-[92%] self-start text-sm">
      <button
        type="button"
        aria-expanded={open}
        className="inline-flex max-w-full items-center gap-1 text-left text-[var(--muted)] hover:text-[var(--fg)]"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="min-w-0">
          {header.shown.map((parts, index) => (
            <span key={index}>
              {index > 0 && ", "}
              <SummaryLabel parts={inSentence(parts, index)} />
            </span>
          ))}
          {header.more > 0 && `, and ${header.more} more`}
          {header.active && (
            <>
              {header.shown.length > 0 && ", "}
              <SummaryLabel parts={inSentence(header.active, header.shown.length)} />
            </>
          )}
        </span>
        <Disclosure open={open} />
      </button>
      {single && <ToolPreview item={single} onPptxShapePicked={onPptxShapePicked} />}
      {open && (
        <div className="mt-2 overflow-hidden rounded-xl border border-[var(--border)]">
          {single ? (
            <ToolDetail item={single} onApprove={onApprove} onPptxShapePicked={onPptxShapePicked} />
          ) : (
            group.items.map((item, index) => (
              <ToolStepRow
                key={item.id}
                item={item}
                running={isRunning(item, live)}
                first={index === 0}
                onApprove={onApprove}
                onPptxShapePicked={onPptxShapePicked}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

/** One call inside an opened run. */
function ToolStepRow({
  item,
  running,
  first,
  onApprove,
  onPptxShapePicked,
}: {
  item: ToolOrApprovalItem;
  running: boolean;
  first: boolean;
  onApprove: (id: string, approved: boolean) => void;
  onPptxShapePicked: (capture: PptxShapeCapture) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={first ? "" : "border-t border-[var(--border)]"}>
      <button
        type="button"
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-[var(--card-bg)]"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="min-w-0 flex-1 truncate">
          <SummaryLabel parts={summarizeItemParts(item, running)} />
        </span>
        <span className="text-[var(--muted)]">
          <Disclosure open={open} />
        </span>
      </button>
      <div className="px-3">
        <ToolPreview item={item} onPptxShapePicked={onPptxShapePicked} />
      </div>
      {open && (
        <div className="border-t border-[var(--border)]">
          <ToolDetail item={item} onApprove={onApprove} onPptxShapePicked={onPptxShapePicked} />
        </div>
      )}
    </div>
  );
}

/** A finished write_docx/xlsx/pptx call's thumbnail; a .pptx one is
 * clickable to target a shape in a follow-up edit. */
function ToolPreview({
  item,
  onPptxShapePicked,
}: {
  item: ToolOrApprovalItem;
  onPptxShapePicked: (capture: PptxShapeCapture) => void;
}) {
  const previewPath = previewPathOf(item.result);
  if (!previewPath) return null;
  const pptxTarget = pptxOverlayTargetOf(item.arguments);
  const src = `/api/previews/${encodeURIComponent(previewPath)}`;
  const className = "my-1.5 block max-h-32 rounded-lg border border-[var(--border)]";
  return pptxTarget ? (
    <PptxShapeOverlay
      src={src}
      alt={`${item.toolName} preview`}
      className={className}
      path={pptxTarget.path}
      slide={pptxTarget.slide}
      onPick={onPptxShapePicked}
    />
  ) : (
    <img src={src} alt={`${item.toolName} preview`} className={className} />
  );
}

/** What an opened call shows: its result, or for an approval, what was
 * asked and decided. */
function ToolDetail({
  item,
  onApprove,
  onPptxShapePicked,
}: {
  item: ToolOrApprovalItem;
  onApprove: (id: string, approved: boolean) => void;
  onPptxShapePicked: (capture: PptxShapeCapture) => void;
}) {
  if (item.kind === "approval") {
    return (
      <div className="px-3 py-2">
        <ApprovalDetail item={item} onApprove={onApprove} onPptxShapePicked={onPptxShapePicked} />
      </div>
    );
  }
  if (item.result === undefined) {
    return <p className="px-3 py-2 text-[var(--muted)]">No result yet.</p>;
  }
  return (
    <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all bg-[var(--code-bg)] px-3 py-2 font-mono text-xs leading-relaxed">
      {typeof item.result === "string" ? item.result : JSON.stringify(item.result, null, 2)}
    </pre>
  );
}

/** A call waiting on the user's approval: a bordered card, open by
 * default, with the Approve/Deny controls. */
function ToolCallRow({
  item,
  onApprove,
  onPptxShapePicked,
}: {
  item: ToolOrApprovalItem;
  onApprove: (id: string, approved: boolean) => void;
  onPptxShapePicked: (capture: PptxShapeCapture) => void;
}) {
  const [open, setOpen] = useState(() => item.kind === "approval" && item.status === "pending");
  const isPendingApproval = item.kind === "approval" && item.status === "pending";

  return (
    <div
      className={`max-w-[92%] self-start rounded-xl border bg-[var(--card-bg)] px-3 py-2 text-sm ${
        isPendingApproval ? "border-[var(--accent)]" : "border-[var(--border)] hover:bg-[var(--panel-bg)]"
      }`}
    >
      <button
        type="button"
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 text-left text-[var(--fg)]"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="truncate">
          <SummaryLabel parts={summarizeItemParts(item)} />
        </span>
        <span className="text-[var(--muted)]">
          <Disclosure open={open} />
        </span>
      </button>
      <ToolPreview item={item} onPptxShapePicked={onPptxShapePicked} />
      {open && (
        <div className="mt-1.5">
          {item.kind === "tool" ? (
            item.result !== undefined && (
              <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-all">
                {typeof item.result === "string" ? item.result : JSON.stringify(item.result, null, 2)}
              </pre>
            )
          ) : (
            <ApprovalDetail item={item} onApprove={onApprove} onPptxShapePicked={onPptxShapePicked} />
          )}
        </div>
      )}
    </div>
  );
}

/** A pptx edit's before/after slide render, side by side -- what
 * web/session.py's _build_pptx_edit_preview sends alongside a raw
 * arguments dump, so approving `{"shape_index": 3, "fill_color":
 * "38BDF8"}` doesn't require reading JSON to picture the result. Either
 * side can be missing on its own (the dry run failed, or there was
 * nothing to diff against) -- rendered as a muted placeholder rather than
 * collapsing the layout, so "before" and "after" always line up.
 * `pptxTarget` (present only for a real .pptx edit -- see
 * pptxOverlayTargetOf) makes both sides clickable the same way
 * ToolCallRow's own completed-call thumbnail is, so picking a shape to
 * target a *follow-up* edit doesn't require waiting for this one to
 * resolve first. */
function ApprovalPreview({
  beforePreview,
  afterPreview,
  pptxTarget,
  onPptxShapePicked,
}: {
  beforePreview?: string | null;
  afterPreview?: string | null;
  pptxTarget: { path: string; slide: number } | null;
  onPptxShapePicked: (capture: PptxShapeCapture) => void;
}) {
  return (
    <div className="mb-1.5 grid grid-cols-2 gap-2">
      {(
        [
          ["Before", beforePreview],
          ["After", afterPreview],
        ] as const
      ).map(([label, name]) => (
        <div key={label} className="flex flex-col gap-1">
          <div className="text-[10px] font-medium uppercase tracking-wide text-[var(--muted)]">{label}</div>
          {name ? (
            pptxTarget ? (
              <PptxShapeOverlay
                src={`/api/previews/${encodeURIComponent(name)}`}
                alt={`${label} the edit`}
                className="block w-full rounded-lg border border-[var(--border)]"
                path={pptxTarget.path}
                slide={pptxTarget.slide}
                onPick={onPptxShapePicked}
              />
            ) : (
              <img
                src={`/api/previews/${encodeURIComponent(name)}`}
                alt={`${label} the edit`}
                className="block w-full rounded-lg border border-[var(--border)]"
              />
            )
          ) : (
            <div className="flex aspect-[4/3] w-full items-center justify-center rounded-lg border border-dashed border-[var(--border)] text-[10px] text-[var(--muted)]">
              no preview
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/** run_python_script/run_node_script/run_background_script are the tools
 * with no sandbox around them -- the script text IS the entire safety
 * review, so they get their own larger, clearly-labeled code block
 * instead of being buried, JSON-escaped, inside a generic arguments dump
 * the way every other tool's args are. run_background_script's arguments
 * carry the same script/description shape as the other two (see
 * tools/background_tasks.py), so it reuses this exact rendering. */
function ApprovalDetail({
  item,
  onApprove,
  onPptxShapePicked,
}: {
  item: Extract<LogItem, { kind: "approval" }>;
  onApprove: (id: string, approved: boolean) => void;
  onPptxShapePicked: (capture: PptxShapeCapture) => void;
}) {
  const isScript =
    item.toolName === "run_python_script" ||
    item.toolName === "run_node_script" ||
    item.toolName === "run_background_script";
  const scriptArgs = item.arguments;
  const hasPreview = Boolean(item.beforePreview || item.afterPreview);
  const pptxTarget = pptxOverlayTargetOf(item.arguments);
  return (
    <>
      {hasPreview && (
        <ApprovalPreview
          beforePreview={item.beforePreview}
          afterPreview={item.afterPreview}
          pptxTarget={pptxTarget}
          onPptxShapePicked={onPptxShapePicked}
        />
      )}
      {isScript ? (
        <div className="flex flex-col gap-1.5">
          {typeof scriptArgs.description === "string" && scriptArgs.description && (
            <div className="text-xs text-[var(--muted)]">{scriptArgs.description}</div>
          )}
          <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap break-all rounded-md bg-black/20 px-2 py-1.5 font-mono text-xs leading-normal">
            {typeof scriptArgs.script === "string" ? scriptArgs.script : JSON.stringify(scriptArgs.script)}
          </pre>
          <div className="text-xs text-[var(--muted)]">
            This script runs with no sandbox -- it can read/write any file this app can, and reach the network.
            Review it before approving.
          </div>
        </div>
      ) : (
        <pre className="max-h-32 overflow-y-auto whitespace-pre-wrap break-all text-xs text-[var(--muted)]">{JSON.stringify(item.arguments, null, 2)}</pre>
      )}
      {item.status === "pending" ? (
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            className="rounded-md bg-[var(--accent)] px-3 py-1 text-sm text-[var(--accent-fg)]"
            onClick={() => onApprove(item.id, true)}
          >
            Approve
          </button>
          <button
            type="button"
            className="rounded-md border border-[var(--border)] px-3 py-1 text-sm"
            onClick={() => onApprove(item.id, false)}
          >
            Deny
          </button>
        </div>
      ) : (
        <>
          <div className="mt-1 text-xs text-[var(--muted)]">{item.status === "approved" ? "Approved" : "Denied"}</div>
          {item.result !== undefined && (
            <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap break-all text-xs text-[var(--muted)]">
              {typeof item.result === "string" ? item.result : JSON.stringify(item.result, null, 2)}
            </pre>
          )}
        </>
      )}
    </>
  );
}

/** A user bubble with a hover-revealed edit affordance -- clicking it
 * swaps the bubble for an inline textarea; submitting truncates the
 * conversation at this turn and regenerates from the edited text (see
 * EditMessageOut's docstring in wire.ts and _handle_edit_message_locked
 * in web/session.py for what happens server-side). Its own useState (not
 * lifted into ChatState) mirrors ToolCallRow/ToolRunGroupView's existing
 * pattern of local, ephemeral UI state that doesn't need to survive a
 * remount or be visible to any other component. */
// A user bubble past this length collapses behind "Show more" -- mainly
// hit by a pasted-content turn (Composer.tsx's submit() now puts the real
// pasted text in displayText instead of dropping it), where the raw text
// can otherwise be long enough to push the rest of the conversation off
// screen. Same magnitude as Composer's own PASTE_CARD_MIN_CHARS, so "long
// enough to collapse" means the same thing on both sides of a send.
const LONG_MESSAGE_COLLAPSE_CHARS = 1000;

function UserMessageView({
  item,
  onEditMessage,
}: {
  item: Extract<LogItem, { kind: "user" }>;
  onEditMessage?: (turnIndex: number, text: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.text);
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  const [textExpanded, setTextExpanded] = useState(false);
  const isLongText = item.text.length > LONG_MESSAGE_COLLAPSE_CHARS;
  const shownText =
    isLongText && !textExpanded ? `${item.text.slice(0, LONG_MESSAGE_COLLAPSE_CHARS)}…` : item.text;

  const cancel = () => {
    setDraft(item.text);
    setEditing(false);
  };

  const submit = () => {
    if (!draft.trim() || !onEditMessage) return;
    onEditMessage(item.turnIndex, draft);
    setEditing(false);
  };

  if (editing) {
    return (
      <div className="ml-auto flex max-w-[96%] flex-col items-end gap-1.5">
        <textarea
          autoFocus
          className="w-full resize-none rounded-2xl border border-[var(--accent)] bg-[var(--user-bubble)] px-4 py-2 text-[var(--user-bubble-fg)] outline-none"
          rows={Math.min(10, draft.split("\n").length)}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              cancel();
            } else if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              submit();
            }
          }}
        />
        <div className="flex gap-1.5">
          <button
            type="button"
            className="rounded-md border border-[var(--border)] px-2.5 py-1 text-xs hover:bg-[var(--card-bg)]"
            onClick={cancel}
          >
            Cancel
          </button>
          <button
            type="button"
            className="rounded-md bg-[var(--accent)] px-2.5 py-1 text-xs text-[var(--accent-fg)] disabled:opacity-40"
            onClick={submit}
            disabled={!draft.trim()}
          >
            Save &amp; submit
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="ml-auto flex max-w-[96%] flex-col items-end gap-1.5">
      {item.images && item.images.length > 0 && (
        <div className="flex flex-wrap justify-end gap-1.5">
          {item.images.map((src, i) => (
            <button
              key={i}
              type="button"
              title="Preview image"
              className="h-16 w-16 cursor-zoom-in overflow-hidden rounded-lg border border-[var(--border)]"
              onClick={() => setLightboxSrc(src)}
            >
              <img src={src} alt={`Attachment ${i + 1}`} className="h-full w-full object-cover" />
            </button>
          ))}
        </div>
      )}
      <div className="group/msg flex items-center gap-1">
        {onEditMessage && (
          <button
            type="button"
            title="Edit message"
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[var(--muted)] opacity-0 transition-opacity hover:bg-[var(--card-bg)] hover:text-[var(--fg)] group-hover/msg:opacity-100"
            onClick={() => setEditing(true)}
          >
            <PencilIcon className="h-3.5 w-3.5" />
          </button>
        )}
        <div className="rounded-2xl bg-[var(--user-bubble)] px-4 py-2 text-[var(--user-bubble-fg)] whitespace-pre-wrap">
          {shownText}
        </div>
      </div>
      {isLongText && (
        <button
          type="button"
          className="text-xs text-[var(--muted)] hover:text-[var(--fg)] hover:underline"
          onClick={() => setTextExpanded((prev) => !prev)}
        >
          {textExpanded ? "Show less" : "Show more"}
        </button>
      )}
      {lightboxSrc && (
        <ImageLightbox src={lightboxSrc} alt="Attachment preview" onClose={() => setLightboxSrc(null)} />
      )}
    </div>
  );
}

function LogItemView({
  item,
  onEditMessage,
}: {
  item: Extract<LogItem, { kind: "user" | "agent" | "system" }>;
  onEditMessage?: (turnIndex: number, text: string) => void;
}) {
  if (item.kind === "user") {
    if (item.text.startsWith(RUN_PROMPT_PREFIX)) return <ScheduledRunCard text={item.text} />;
    return <UserMessageView item={item} onEditMessage={onEditMessage} />;
  }

  if (item.kind === "agent") {
    const text = item.streaming ? `${item.text} ▍` : item.text;
    return (
      <div className="min-w-0 max-w-[92%] [overflow-wrap:anywhere]">
        {/* No border/bubble at all, like claude.ai's own assistant replies
         * -- the earlier border-l-2 "anchor" (dc76b88) was real, live
         * user feedback at the time, but became its own live complaint
         * later ("那条竖线不好看"). The muted tool-call summary line
         * directly above already reads as visually distinct from this
         * full-contrast prose without needing a line to separate them --
         * verified via screenshot, not just reasoned about. Copy used to
         * live here too (hover-revealed, per message) -- now one copy
         * button per turn instead, see TurnView's own footer. */}
        <div className="py-1 text-[var(--agent-bubble-fg)]">
          <Suspense fallback={<div className="whitespace-pre-wrap">{text}</div>}>
            <Markdown text={text} />
          </Suspense>
        </div>
      </div>
    );
  }

  return (
    <div className="self-start text-xs text-[var(--muted)]">
      {item.text}
      <span className="ml-0.5">&rsaquo;</span>
    </div>
  );
}
