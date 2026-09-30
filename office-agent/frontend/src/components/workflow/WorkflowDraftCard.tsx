import { useContext, useState } from "react";
import type { WorkflowDraftEntry, WorkflowTestResult } from "../../lib/transcriptGrouping";
import { type DraftCards, DraftCardsContext } from "./draftCards";
import { placeSteps } from "../../lib/workflowTree";
import { primaryButton, secondaryButton } from "../../lib/formStyles";
import {
  AlertCircleIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  FileTextIcon,
  WorkflowIcon,
  XCircleIcon,
} from "../icons";
import { StepKindTile } from "./parts";
import { Screenshot } from "./Screenshot";

type Status =
  | { kind: "saved"; name: string; triggerId: string }
  | { kind: "testing"; done: number; total: number }
  | { kind: "passed"; result: WorkflowTestResult }
  | { kind: "approval"; result: WorkflowTestResult }
  | { kind: "failed"; result: WorkflowTestResult }
  | { kind: "untested" };

function draftStatus(entry: WorkflowDraftEntry, cards: DraftCards): Status {
  const saved = entry.draftId ? cards.saved.get(entry.draftId) : undefined;
  if (saved) return { kind: "saved", ...saved };
  const test = entry.draftId ? cards.tests.get(entry.draftId) : undefined;
  if (test?.running) {
    const total = cards.testProgress?.total ?? placeSteps(entry.workflow.steps).length;
    return { kind: "testing", done: cards.testProgress?.done ?? 0, total };
  }
  const result = test?.result;
  if (!result) return { kind: "untested" };
  if (result.status === "passed") return { kind: "passed", result };
  if (result.status === "stopped_at_approval") return { kind: "approval", result };
  return { kind: "failed", result };
}

function failedTitle(result: WorkflowTestResult): string {
  return result.steps.find((s) => s.id === result.failed_step)?.title ?? result.failed_step ?? "a step";
}

function StatusLine({ status }: { status: Status }) {
  const base = "flex min-w-0 items-center gap-1.5 text-[13px]";
  switch (status.kind) {
    case "saved":
      return (
        <span className={`${base} text-[var(--success)]`}>
          <CheckCircleIcon className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">Saved to “{status.name}”</span>
        </span>
      );
    case "testing":
      return (
        <span className={`${base} text-[var(--fg)]`} data-testid="draft-testing">
          <span className="h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-[color-mix(in_srgb,var(--fg)_15%,transparent)] border-t-[var(--accent)] motion-reduce:animate-none" />
          Testing {status.done}/{status.total}
          <span className="ml-1 h-1 w-24 overflow-hidden rounded-full bg-[var(--card-bg)]">
            <span
              className="block h-full rounded-full bg-[var(--accent)] transition-[width]"
              style={{ width: `${status.total ? (100 * status.done) / status.total : 0}%` }}
            />
          </span>
        </span>
      );
    case "passed":
      return (
        <span className={`${base} text-[var(--success)]`}>
          <CheckCircleIcon className="h-3.5 w-3.5 shrink-0" />
          Tested · every step passed in {status.result.seconds}s
        </span>
      );
    case "approval":
      return (
        <span className={`${base} text-[var(--warning)]`}>
          <AlertCircleIcon className="h-3.5 w-3.5 shrink-0" />
          Tested up to an approval step, where a test stops
        </span>
      );
    case "failed":
      return (
        <span className={`${base} text-[var(--danger)]`}>
          <XCircleIcon className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">Test failed at “{failedTitle(status.result)}”</span>
        </span>
      );
    default:
      return <span className={`${base} text-[var(--muted)]`}>Not tested yet</span>;
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function Evidence({ result }: { result: WorkflowTestResult }) {
  const failed = result.status === "failed";
  if (!failed && result.files.length === 0 && !result.screenshot) return null;
  return (
    <div className="mt-3 flex flex-col gap-2.5">
      {failed && (
        <div className="rounded-lg border border-[color-mix(in_srgb,var(--danger)_35%,transparent)] bg-[color-mix(in_srgb,var(--danger)_6%,transparent)] px-3 py-2 text-[13px]">
          <div className="font-medium">{failedTitle(result)}</div>
          {result.error && (
            <div className="mt-0.5 line-clamp-3 break-words text-[var(--muted)]">{result.error}</div>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-start gap-3">
        {result.files.length > 0 && (
          <ul className="flex min-w-0 flex-1 flex-col gap-1.5" data-testid="draft-test-files">
            {result.files.map((file) => (
              <li key={file.path} className="flex min-w-0 items-center gap-2 text-[13px]">
                <FileTextIcon className="h-3.5 w-3.5 shrink-0 text-[var(--muted)]" />
                <span className="min-w-0 truncate" title={file.path}>
                  {file.path}
                </span>
                <span className="shrink-0 tabular-nums text-[var(--muted)]">
                  {file.rows !== undefined && file.columns !== undefined
                    ? `${file.rows} rows × ${file.columns} columns · `
                    : ""}
                  {formatSize(file.size)}
                </span>
              </li>
            ))}
          </ul>
        )}
        {result.screenshot && <Screenshot name={result.screenshot} alt="The page the test ended on" />}
      </div>
    </div>
  );
}

function stepMark(status: string | undefined) {
  if (status === "done") return <CheckCircleIcon className="h-3.5 w-3.5 shrink-0 text-[var(--success)]" />;
  if (status === "failed") return <XCircleIcon className="h-3.5 w-3.5 shrink-0 text-[var(--danger)]" />;
  if (status === "waiting") return <AlertCircleIcon className="h-3.5 w-3.5 shrink-0 text-[var(--warning)]" />;
  return <span className="h-3.5 w-3.5 shrink-0" />;
}

function Steps({ entry, result }: { entry: WorkflowDraftEntry; result: WorkflowTestResult | null }) {
  const [open, setOpen] = useState(false);
  const placed = placeSteps(entry.workflow.steps);
  const inputs = entry.workflow.inputs.length;
  const byId = new Map((result?.steps ?? []).map((s) => [s.id, s]));
  const summary = [
    `${placed.length} ${placed.length === 1 ? "step" : "steps"}`,
    inputs > 0 ? `${inputs} ${inputs === 1 ? "input" : "inputs"} asked each run` : "no inputs",
  ].join(" · ");
  return (
    <div className="mt-3">
      <button
        type="button"
        aria-expanded={open}
        className="-ml-1 flex items-center gap-1 rounded-md px-1 py-0.5 text-[13px] text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronDownIcon className="h-3.5 w-3.5" /> : <ChevronRightIcon className="h-3.5 w-3.5" />}
        {summary}
      </button>
      {open && (
        <ol className="mt-2 flex flex-col gap-1.5">
          {placed.map(({ step, depth }) => {
            const tested = byId.get(step.id);
            return (
              <li
                key={step.id}
                className="flex min-w-0 items-center gap-2.5 text-[13px]"
                style={{ paddingLeft: depth * 20 }}
              >
                {result && stepMark(tested?.status)}
                <StepKindTile step={step} size="sm" />
                <span className={`truncate ${tested?.status === "not reached" ? "text-[var(--muted)]" : ""}`}>
                  {step.title}
                </span>
                {tested?.seconds != null && (
                  <span className="ml-auto shrink-0 tabular-nums text-xs text-[var(--muted)]">{tested.seconds}s</span>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

/** A fixed workflow the model drafted or revised in this conversation,
 * results first: whether its test passed and what it produced. Nothing
 * is saved until the user reviews it and saves. */
export function WorkflowDraftCard({ entry }: { entry: WorkflowDraftEntry }) {
  const cards = useContext(DraftCardsContext);
  const superseded = cards.latestDraftId !== null && entry.id !== cards.latestDraftId;
  const status = draftStatus(entry, cards);
  const revision = entry.triggerId !== null;
  if (superseded) {
    return (
      <div className="flex min-w-0 items-center gap-2 text-[13px] text-[var(--muted)]">
        <WorkflowIcon className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 truncate">
          Earlier draft: <span className="text-[var(--fg)]">{entry.name}</span> -- a newer one follows
        </span>
        <button
          type="button"
          disabled={!cards.onReview}
          className="shrink-0 rounded-md px-1.5 py-0.5 hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
          onClick={() => cards.onReview?.(entry)}
        >
          Review
        </button>
      </div>
    );
  }
  const test = entry.draftId ? cards.tests.get(entry.draftId) : undefined;
  const result = test && !test.running ? test.result : null;
  const busy = status.kind === "testing";

  return (
    <div
      className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4"
      data-testid="workflow-draft-card"
      data-draft-card={entry.id}
    >
      <div className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
        <WorkflowIcon className="h-3.5 w-3.5" />
        {revision ? "Workflow changes" : "Fixed workflow draft"}
      </div>
      <div className="font-medium">{entry.name}</div>
      <div className="mt-1">
        <StatusLine status={status} />
      </div>

      {result && <Evidence result={result} />}

      {entry.changes.length > 0 && !revision && (
        <div className="mt-3 text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
          Changed since the last draft
        </div>
      )}
      {entry.changes.length > 0 && (
        <ul className={`${revision ? "mt-3" : "mt-1.5"} flex list-disc flex-col gap-1 pl-5 text-[13px] leading-relaxed marker:text-[var(--muted)]`}>
          {entry.changes.map((change, index) => (
            <li key={index}>{change}</li>
          ))}
        </ul>
      )}

      <Steps entry={entry} result={result} />

      <div className="mt-3.5 flex flex-wrap items-center gap-2">
        {status.kind === "saved" ? (
          <button
            type="button"
            className={secondaryButton}
            disabled={!cards.onOpenTask}
            onClick={() => cards.onOpenTask?.(status.triggerId)}
          >
            Open task
          </button>
        ) : (
          <>
            <button
              type="button"
              className={primaryButton}
              disabled={!cards.onReview}
              onClick={() => cards.onReview?.(entry)}
            >
              {revision ? "Review changes" : "Review and save"}
            </button>
            {entry.draftId && (
              <button
                type="button"
                className={secondaryButton}
                disabled={!cards.onTestAgain || busy}
                onClick={() => cards.onTestAgain?.(entry)}
              >
                {result ? "Test again" : "Test it"}
              </button>
            )}
            <button
              type="button"
              className="rounded-md px-2 py-1.5 text-[13px] text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
              disabled={!cards.onContinue}
              onClick={() => cards.onContinue?.()}
            >
              Continue in chat
            </button>
          </>
        )}
        {entry.notes.length > 0 && status.kind !== "saved" && (
          <span className="ml-auto flex items-center gap-1.5 text-[13px] text-[var(--muted)]">
            <AlertCircleIcon className="h-3.5 w-3.5 text-[var(--warning)]" />
            {entry.notes.length} {entry.notes.length === 1 ? "thing" : "things"} worth checking
          </span>
        )}
      </div>
    </div>
  );
}

/** The newest unsaved draft, kept in reach at the bottom of the
 * conversation while its card is scrolled out of view. */
export function PinnedDraftBar({ entry, onShow }: { entry: WorkflowDraftEntry; onShow: () => void }) {
  const cards = useContext(DraftCardsContext);
  const status = draftStatus(entry, cards);
  return (
    <div
      className="sticky bottom-2 z-10 flex min-w-0 items-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--bg)] px-3.5 py-2.5 shadow-[0_4px_16px_rgba(0,0,0,0.08)]"
      data-testid="pinned-draft"
    >
      <WorkflowIcon className="h-4 w-4 shrink-0 text-[var(--muted)]" />
      <button
        type="button"
        className="min-w-0 flex-1 text-left"
        title="Show the draft"
        onClick={onShow}
      >
        <div className="truncate text-sm font-medium">{entry.name}</div>
        <StatusLine status={status} />
      </button>
      <button
        type="button"
        className={primaryButton}
        disabled={!cards.onReview}
        onClick={() => cards.onReview?.(entry)}
      >
        {entry.triggerId ? "Review changes" : "Review and save"}
      </button>
    </div>
  );
}
