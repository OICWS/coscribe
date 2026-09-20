import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { createScheduledTask, updateScheduledTask } from "../lib/rest";
import type { ApprovalMode, ScheduledTask, ScheduleKind } from "../types/settings";
import { CloseIcon, FolderIcon } from "./icons";

const FREQUENCY_OPTIONS: { value: ScheduleKind; label: string }[] = [
  { value: "manual", label: "Manual" },
  { value: "hourly", label: "Hourly" },
  { value: "daily", label: "Daily" },
  { value: "weekdays", label: "Weekdays" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
];

const APPROVAL_OPTIONS: { value: ApprovalMode; label: string }[] = [
  { value: "manual", label: "Ask before every action" },
  { value: "auto", label: "Automatically approve" },
  { value: "skip", label: "Never ask" },
];

// Display order matches the reference screenshot (Sunday first, JS
// Date.getDay() order); `value` is ScheduleRule.weekday's own convention
// (0=Monday..6=Sunday, see tools/scheduled_tasks.py) -- the two orders
// differ, so this table (not a formula) is what bridges them.
const WEEKDAY_OPTIONS: { label: string; value: number }[] = [
  { label: "Sunday", value: 6 },
  { label: "Monday", value: 0 },
  { label: "Tuesday", value: 1 },
  { label: "Wednesday", value: 2 },
  { label: "Thursday", value: 3 },
  { label: "Friday", value: 4 },
  { label: "Saturday", value: 5 },
];

// No banner for "manual" -- it's the safe default, and no reference
// screenshot shows one for it (only "auto" does). "coscribe," not
// "Claude" -- this app's own product name, not a verbatim copy of the
// reference's own wording (that screenshot is Claude Cowork's own UI).
const APPROVAL_BANNER: Partial<Record<ApprovalMode, string>> = {
  auto: "For this task, coscribe will work and use connectors without pausing for approval.",
  skip: "For this task, coscribe will never pause for approval, even for actions that would otherwise need a second look. Use with caution.",
};

function todayIso(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function dateForDayOfMonth(day: number): string {
  const today = new Date();
  const candidate = new Date(today.getFullYear(), today.getMonth(), day);
  if (candidate < today) candidate.setMonth(candidate.getMonth() + 1);
  return `${candidate.getFullYear()}-${String(candidate.getMonth() + 1).padStart(2, "0")}-${String(candidate.getDate()).padStart(2, "0")}`;
}

/** Best-effort reconstruction of the "starts on" date to show when
 * editing a monthly task -- the only kind that still uses a date picker
 * (weekly uses a plain weekday dropdown instead, see WEEKDAY_OPTIONS).
 * ScheduleRule itself only stores a bare day_of_month number, not a
 * specific date, so this picks the nearest real date matching it (or
 * start_date, if the trigger has one) for display purposes only;
 * submitting the form re-derives day_of_month/start_date from whatever
 * date ends up picked. */
function initialDateFor(task: ScheduledTask | null): string {
  if (!task) return todayIso();
  if (task.schedule.start_date) return task.schedule.start_date;
  if (task.schedule.kind === "monthly") return dateForDayOfMonth(task.schedule.day_of_month ?? 1);
  return todayIso();
}

interface ProviderInfo {
  default_model: string;
}

interface ScheduledTaskModalProps {
  task: ScheduledTask | null; // null = create; otherwise editing this task
  onClose: () => void;
  onSaved: () => void;
}

/** The Create/Edit scheduled task form -- matches
 * docs/ui-references/sheduled-edit-tasks.png (edit) and the analogous
 * "Create scheduled task" screenshot pixel-for-pixel in layout; reachable
 * from RunPanel's "New task" menu, a task card/sidebar row's "Edit"
 * action, and the detail page's pencil icon (all pass a different `task`
 * prop into the same component rather than duplicating the form three
 * times). Deliberately drops the old inline form's prompt-vs-saved-
 * workflow tab -- the reference has no such control, just one
 * "Instructions" field, and an explicit ask was to match the form's shape
 * first, real content/behavior gaps later. A workflow-backed trigger
 * created before this modal existed still runs fine; editing it through
 * here switches it to a plain prompt (workflow_name is never sent). */
export function ScheduledTaskModal({ task, onClose, onSaved }: ScheduledTaskModalProps) {
  const isEdit = task !== null;
  const [name, setName] = useState(task?.name ?? "");
  const [instructions, setInstructions] = useState(task?.prompt ?? "");
  const [model, setModel] = useState(task?.model ?? "");
  const [kind, setKind] = useState<ScheduleKind>(task?.schedule.kind === "once" ? "manual" : (task?.schedule.kind ?? "manual"));
  const [date, setDate] = useState(initialDateFor(task));
  const [time, setTime] = useState(task?.schedule.at && task.schedule.kind !== "manual" ? task.schedule.at : "09:00");
  const [weekday, setWeekday] = useState(task?.schedule.weekday ?? 0);
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>(task?.approval_mode ?? "manual");
  const [providers, setProviders] = useState<[string, ProviderInfo][]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/providers")
      .then((res) => res.json())
      .then((data: Record<string, ProviderInfo>) => {
        setProviders(Object.entries(data).filter(([, info]) => info.default_model));
      })
      .catch(() => {});
  }, []);

  const save = async () => {
    const trimmedName = name.trim();
    const trimmedInstructions = instructions.trim();
    if (!trimmedName) {
      setError("Name is required.");
      return;
    }
    if (!trimmedInstructions) {
      setError("Instructions are required.");
      return;
    }
    const payload = {
      name: trimmedName,
      kind,
      at: kind === "manual" ? "" : kind === "hourly" ? "00:00" : time,
      prompt: trimmedInstructions,
      ...(kind === "weekly" ? { weekday } : {}),
      ...(kind === "monthly" ? { day_of_month: Number(date.split("-")[2]), start_date: date } : {}),
      ...(model ? { model } : {}),
      approval_mode: approvalMode,
    };
    setSaving(true);
    setError(null);
    const result = isEdit ? await updateScheduledTask(task.trigger_id, payload) : await createScheduledTask(payload);
    setSaving(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    onSaved();
    onClose();
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="flex max-h-[90vh] w-[min(720px,100vw-2rem)] flex-col rounded-[16px] border border-[var(--border)] bg-[var(--panel-bg)] p-6 shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">{isEdit ? "Edit scheduled task" : "Create scheduled task"}</h2>
          <button
            type="button"
            aria-label="Close"
            className="flex h-7 w-7 items-center justify-center rounded-md border border-[var(--border)] text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
            onClick={onClose}
          >
            <CloseIcon className="h-4 w-4" />
          </button>
        </div>

        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto">
          <div>
            <label className="mb-1 block text-sm font-medium">
              Name <span className="text-[var(--danger)]">*</span>
            </label>
            <input
              className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-sm outline-none focus:border-[var(--accent)]"
              placeholder="Daily briefing"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">
              Instructions <span className="text-[var(--danger)]">*</span>
            </label>
            <div className="overflow-hidden rounded-md border border-[var(--border)]">
              <textarea
                className="w-full resize-none bg-transparent px-3 py-2 text-sm outline-none"
                placeholder="Summarize my calendar and unread emails. Flag anything urgent."
                rows={5}
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
              />
              <div className="flex items-center justify-between border-t border-[var(--border)] bg-[var(--card-bg)] px-3 py-2 text-sm">
                <button
                  type="button"
                  disabled
                  title="Not available yet"
                  className="flex items-center gap-1.5 text-[var(--muted)] opacity-60"
                >
                  <FolderIcon className="h-3.5 w-3.5" /> Select workspace
                </button>
                <select
                  aria-label="Model"
                  className="bg-transparent text-[var(--muted)] outline-none"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                >
                  <option value="">Default model</option>
                  {providers.map(([providerKey, info]) => {
                    const value = `${providerKey}:${info.default_model}`;
                    return (
                      <option key={providerKey} value={value}>
                        {info.default_model}
                      </option>
                    );
                  })}
                </select>
              </div>
            </div>
          </div>

          {/* w-24 on both labels is load-bearing, not decorative -- it's
           * what keeps the Frequency/Permissions dropdowns' left edges
           * aligned despite the two labels being different lengths,
           * matching sheduled-edit-tasks.png's layout. */}
          <div className="flex flex-wrap items-center gap-2">
            <label className="w-24 shrink-0 text-sm font-medium">Frequency</label>
            <select
              aria-label="Frequency"
              className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1.5 text-sm"
              value={kind}
              onChange={(e) => setKind(e.target.value as ScheduleKind)}
            >
              {FREQUENCY_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
            {/* manual/hourly show no further controls at all -- hourly
             * always fires on the hour (:00), not user-configurable from
             * here (the backend itself supports an arbitrary minute, see
             * tools/scheduled_tasks.py's compute_next_run_at, but the
             * reference screenshots never expose that). daily/weekdays
             * add just a time; weekly adds a time + a weekday dropdown
             * (not a date -- ScheduleRule.weekday is a bare 0-6, no
             * specific calendar date involved); monthly alone gets the
             * "starts on" date+time pair, since a specific date is the
             * only way to name both a day-of-month and a floor. */}
            {(kind === "daily" || kind === "weekdays" || kind === "weekly") && (
              <input
                type="time"
                className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1.5 text-sm outline-none"
                value={time}
                onChange={(e) => setTime(e.target.value)}
              />
            )}
            {kind === "weekly" && (
              <select
                aria-label="Weekday"
                className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1.5 text-sm"
                value={weekday}
                onChange={(e) => setWeekday(Number(e.target.value))}
              >
                {WEEKDAY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            )}
            {kind === "monthly" && (
              <>
                <span className="text-sm text-[var(--muted)]">starts on</span>
                <input
                  type="date"
                  className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1.5 text-sm outline-none"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                />
                <input
                  type="time"
                  className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1.5 text-sm outline-none"
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                />
              </>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <label className="w-24 shrink-0 text-sm font-medium">Permissions</label>
            <select
              aria-label="Permissions"
              className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1.5 text-sm"
              value={approvalMode}
              onChange={(e) => setApprovalMode(e.target.value as ApprovalMode)}
            >
              {APPROVAL_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          {APPROVAL_BANNER[approvalMode] && (
            <div className="rounded-md border border-[var(--border)] px-3 py-2 text-sm text-[var(--muted)]">
              {APPROVAL_BANNER[approvalMode]}
            </div>
          )}

          {error && <div className="text-sm text-[var(--danger)]">{error}</div>}
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            className="rounded-md border border-[var(--border)] px-4 py-1.5 text-sm hover:bg-[var(--card-bg)]"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={saving}
            className="rounded-md bg-[var(--primary)] px-4 py-1.5 text-sm font-medium text-[var(--primary-fg)] disabled:opacity-60"
            onClick={save}
          >
            Save
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
