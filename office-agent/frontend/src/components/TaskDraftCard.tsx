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

const FIELD_LABELS: Record<string, string> = {
  name: "name",
  kind: "schedule",
  at: "time",
  prompt: "instructions",
  weekday: "day",
  day_of_month: "day",
  start_date: "start date",
  model: "model",
  approval_mode: "approval setting",
};

function changedFields(draft: TaskDraft): string {
  const labels = [...new Set((draft.changed ?? []).map((field) => FIELD_LABELS[field] ?? field))];
  return labels.length > 0 ? `Changes its ${labels.join(", ")}` : "No changes";
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
  const isEdit = Boolean(item.draft.trigger_id);
  const name = item.draft.name?.trim() || (isEdit ? "a task" : "Untitled task");

  if (item.status === "saved") {
    return (
      <div className="flex items-center gap-2 text-sm">
        <CheckCircleIcon className="h-4 w-4 shrink-0" style={{ color: "var(--success)" }} />
        <span>
          {isEdit ? "Saved the changes to " : "Saved "}
          <span className="font-medium">{item.savedName ?? name}</span>
          {isEdit ? "" : " to Scheduled"}
        </span>
      </div>
    );
  }
  if (item.status === "dismissed") {
    return (
      <div className="flex items-center gap-2 text-sm text-[var(--muted)]">
        <ClockIcon className="h-4 w-4 shrink-0" />
        <span>
          {isEdit ? "Dismissed the changes to " : "Dismissed the draft of "}
          <span className="font-medium">{name}</span>
        </span>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-[var(--border)] p-4">
      <div className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
        <ClockIcon className="h-3.5 w-3.5" />
        {isEdit ? "Changes to a scheduled task" : "Scheduled task draft"}
      </div>
      <div className="font-medium">{name}</div>
      {isEdit && <div className="mt-0.5 text-[13px] text-[var(--muted)]">{changedFields(item.draft)}</div>}
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
              {isEdit ? "Review changes" : "Review & save"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
