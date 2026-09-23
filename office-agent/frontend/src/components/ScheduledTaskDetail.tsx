import { useState } from "react";
import { deleteScheduledTask, pauseScheduledTask, resumeScheduledTask, runScheduledTaskNow } from "../lib/rest";
import { goToThread } from "../lib/nav";
import { describeSchedule } from "../lib/scheduleLabels";
import type { ScheduledTask } from "../types/settings";
import { ConfirmDialog } from "./ConfirmDialog";
import { PencilIcon, PlayIcon, TrashIcon } from "./icons";
import { ToggleSwitch } from "./ToggleSwitch";

const APPROVAL_LABEL: Record<ScheduledTask["approval_mode"], string> = {
  manual: "Ask before every action",
  auto: "Automatically approve",
  skip: "Never ask",
};

const APPROVAL_DESCRIPTION: Record<ScheduledTask["approval_mode"], string> = {
  manual: "coscribe pauses and waits for your approval before any action that needs it.",
  auto: "coscribe runs on its own and uses connectors without pausing for approval.",
  skip: "coscribe never pauses for approval, even for actions that would otherwise need a second look.",
};

function formatNextRun(nextRunAt: string | null): string {
  if (!nextRunAt) return "Not scheduled";
  return `Next run: ${new Date(nextRunAt).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })}`;
}

interface ScheduledTaskDetailProps {
  task: ScheduledTask;
  onEdit: () => void;
  onDeleted: () => void;
  onChanged: () => void;
}

/** Read-only detail view for one scheduled task -- matches
 * docs/ui-references/sheduled-display-tasks.png. Reached by clicking a
 * task in the sidebar or a card in the portal grid; its pencil icon opens
 * ScheduledTaskModal in edit mode (one of the three confirmed entry
 * points into that modal, see NavRail/RunPanel's own docstrings). */
export function ScheduledTaskDetail({ task, onEdit, onDeleted, onChanged }: ScheduledTaskDetailProps) {
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [running, setRunning] = useState(false);

  const toggleEnabled = async () => {
    if (task.enabled) await pauseScheduledTask(task.trigger_id);
    else await resumeScheduledTask(task.trigger_id);
    onChanged();
  };

  const runNow = async () => {
    setRunning(true);
    await runScheduledTaskNow(task.trigger_id);
    // The refreshed last_run_at is what makes the next click on this task
    // open its conversation rather than this page (App.openScheduledTask).
    setRunning(false);
    onChanged();
    goToThread(task.thread_id);
  };

  const confirmDelete = async () => {
    setDeleteConfirm(false);
    await deleteScheduledTask(task.trigger_id);
    onDeleted();
  };

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-3xl">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold">{task.name}</h1>
          <div className="flex items-center gap-2">
            <button
              type="button"
              aria-label="Edit"
              className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
              onClick={onEdit}
            >
              <PencilIcon className="h-4 w-4" />
            </button>
            <button
              type="button"
              aria-label="Delete"
              className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--danger)]"
              onClick={() => setDeleteConfirm(true)}
            >
              <TrashIcon className="h-4 w-4" />
            </button>
            <button
              type="button"
              disabled={running}
              className="flex items-center gap-1.5 rounded-md bg-[var(--primary)] px-3 py-1.5 text-sm font-medium text-[var(--primary-fg)] disabled:opacity-60"
              onClick={runNow}
            >
              <PlayIcon className="h-3.5 w-3.5" /> {running ? "Running..." : "Run now"}
            </button>
          </div>
        </div>

        <div className="mt-2 flex items-center gap-2">
          <ToggleSwitch on={task.enabled} onClick={toggleEnabled} />
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-medium ${
              task.enabled ? "bg-green-500/15 text-green-600 dark:text-green-400" : "bg-[var(--card-bg)] text-[var(--muted)]"
            }`}
          >
            {task.enabled ? "Active" : "Paused"}
          </span>
          <span className="text-sm text-[var(--muted)]">{formatNextRun(task.next_run_at)}</span>
        </div>

        <div className="mt-4 border-t border-[var(--border)]" />

        <div className="mt-6 flex flex-col gap-6">
          <div>
            <div className="mb-1 text-sm text-[var(--muted)]">Instructions</div>
            <div className="whitespace-pre-wrap text-sm">
              {task.workflow_name ? `Runs workflow: ${task.workflow_name}` : task.prompt}
            </div>
          </div>
          <div>
            <div className="mb-1 text-sm text-[var(--muted)]">Repeats</div>
            <div className="text-sm font-medium">{describeSchedule(task.schedule)}</div>
          </div>
          <div>
            <div className="mb-1 text-sm text-[var(--muted)]">Permissions</div>
            <div className="text-sm font-medium">{APPROVAL_LABEL[task.approval_mode]}</div>
            <div className="text-sm text-[var(--muted)]">{APPROVAL_DESCRIPTION[task.approval_mode]}</div>
          </div>
        </div>
      </div>

      {deleteConfirm && (
        <ConfirmDialog
          title="Delete scheduled task?"
          description={`"${task.name}" will be permanently removed.`}
          onCancel={() => setDeleteConfirm(false)}
          onConfirm={confirmDelete}
        />
      )}
    </div>
  );
}
