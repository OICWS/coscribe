import { describeSchedule } from "../lib/scheduleLabels";
import type { LogItem } from "../state/reducer";
import type { ScheduleKind } from "../types/settings";
import type { TaskDraft } from "../types/wire";
import { CheckCircleIcon, ClockIcon } from "./icons";

type TaskDraftItem = Extract<LogItem, { kind: "task_draft" }>;

function describeDraftSchedule(draft: TaskDraft): string {
  if (!draft.kind) return "Manual";
  return describeSchedule({
    kind: draft.kind as ScheduleKind,
    at: draft.at ?? "",
    weekday: draft.weekday ?? null,
    day_of_month: draft.day_of_month ?? null,
    start_date: draft.start_date ?? null,
  });
}

interface TaskDraftCardProps {
  item: TaskDraftItem;
  onReview?: (item: TaskDraftItem) => void;
  onDismiss?: (item: TaskDraftItem) => void;
}

/** A scheduled task the model drafted from this conversation -- nothing
 * exists until the user reviews it (the prefilled task form) and saves,
 * or dismisses it. Either way the model is told what happened. */
export function TaskDraftCard({ item, onReview, onDismiss }: TaskDraftCardProps) {
  const name = item.draft.name?.trim() || "Untitled task";

  if (item.status === "saved") {
    return (
      <div className="flex items-center gap-2 text-sm">
        <CheckCircleIcon className="h-4 w-4 shrink-0" style={{ color: "var(--success)" }} />
        <span>
          Saved <span className="font-medium">{item.savedName ?? name}</span> to Scheduled
        </span>
      </div>
    );
  }
  if (item.status === "dismissed") {
    return (
      <div className="flex items-center gap-2 text-sm text-[var(--muted)]">
        <ClockIcon className="h-4 w-4 shrink-0" />
        <span>
          Dismissed the draft of <span className="font-medium">{name}</span>
        </span>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-[var(--border)] p-4">
      <div className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
        <ClockIcon className="h-3.5 w-3.5" />
        Scheduled task draft
      </div>
      <div className="font-medium">{name}</div>
      <div className="mt-1.5">
        <span className="inline-block rounded-md bg-green-500/15 px-2 py-0.5 text-xs font-medium text-green-700 dark:text-green-400">
          {describeDraftSchedule(item.draft)}
        </span>
      </div>
      {item.draft.prompt && (
        <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-sm text-[var(--muted)]">{item.draft.prompt}</p>
      )}
      {(onReview || onDismiss) && (
        <div className="mt-3 flex justify-end gap-2">
          {onDismiss && (
            <button
              type="button"
              className="rounded-md px-3 py-1.5 text-sm text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
              onClick={() => onDismiss(item)}
            >
              Dismiss
            </button>
          )}
          {onReview && (
            <button
              type="button"
              className="rounded-md bg-[var(--primary)] px-3 py-1.5 text-sm font-medium text-[var(--primary-fg)] hover:bg-[var(--primary-hover)]"
              onClick={() => onReview(item)}
            >
              Review &amp; save
            </button>
          )}
        </div>
      )}
    </div>
  );
}
