import type { WorkflowDraftEntry } from "../../lib/transcriptGrouping";
import { placeSteps } from "../../lib/workflowTree";
import { primaryButton } from "../../lib/formStyles";
import { AlertCircleIcon, WorkflowIcon } from "../icons";
import { StepKindTile } from "./parts";

const SHOWN_STEPS = 4;

/** A fixed workflow the model drafted from this conversation. Nothing
 * exists until the user reviews it on the draft page and saves it. */
export function WorkflowDraftCard({
  entry,
  onReview,
  superseded = false,
}: {
  entry: WorkflowDraftEntry;
  onReview?: (entry: WorkflowDraftEntry) => void;
  /** A newer draft came later in this conversation. */
  superseded?: boolean;
}) {
  if (superseded) {
    return (
      <div className="flex min-w-0 items-center gap-2 text-[13px] text-[var(--muted)]">
        <WorkflowIcon className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 truncate">
          Earlier draft: <span className="text-[var(--fg)]">{entry.name}</span> -- a newer one follows
        </span>
        <button
          type="button"
          disabled={!onReview}
          className="shrink-0 rounded-md px-1.5 py-0.5 hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
          onClick={() => onReview?.(entry)}
        >
          Review
        </button>
      </div>
    );
  }
  const placed = placeSteps(entry.workflow.steps);
  const top = entry.workflow.steps.slice(0, SHOWN_STEPS);
  const more = entry.workflow.steps.length - top.length;
  const inputs = entry.workflow.inputs.length;
  const facts = [
    `${placed.length} ${placed.length === 1 ? "step" : "steps"}`,
    inputs > 0 ? `${inputs} ${inputs === 1 ? "input" : "inputs"} asked each run` : "no inputs",
  ];

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4">
      <div className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
        <WorkflowIcon className="h-3.5 w-3.5" />
        Fixed workflow draft
      </div>
      <div className="font-medium">{entry.name}</div>
      <div className="mt-0.5 text-[13px] text-[var(--muted)]">{facts.join(" · ")}</div>

      <ol className="mt-3 flex flex-col gap-1.5">
        {top.map((step) => (
          <li key={step.id} className="flex min-w-0 items-center gap-2.5 text-[13px]">
            <StepKindTile step={step} size="sm" />
            <span className="truncate">{step.title}</span>
          </li>
        ))}
        {more > 0 && (
          <li className="pl-[30px] text-[13px] text-[var(--muted)]">
            and {more} more {more === 1 ? "step" : "steps"}
          </li>
        )}
      </ol>

      <div className="mt-3.5 flex flex-wrap items-center gap-3">
        <button type="button" className={primaryButton} disabled={!onReview} onClick={() => onReview?.(entry)}>
          Review and save
        </button>
        {entry.notes.length > 0 && (
          <span className="flex items-center gap-1.5 text-[13px] text-[var(--muted)]">
            <AlertCircleIcon className="h-3.5 w-3.5 text-[var(--warning)]" />
            {entry.notes.length} {entry.notes.length === 1 ? "thing" : "things"} worth checking
          </span>
        )}
      </div>
    </div>
  );
}
