import { useRef, useState } from "react";
import { goToThread } from "../lib/nav";
import { capitalize, formatRunTime, RUN_STATUS_LABEL, runSourceLabel } from "../lib/runLabels";
import { useClickOutside } from "../lib/useClickOutside";
import type { ScheduledTask } from "../types/settings";
import { CheckIcon, ChevronDownIcon, ChevronRightIcon, ClockIcon } from "./icons";
import { RunStatusIcon } from "./RunStatusIcon";

const MAX_RUNS_LISTED = 15;

interface RunBreadcrumbProps {
  task: ScheduledTask | null;
  threadId: string;
  onOpenPortal: () => void;
  onOpenTask: (task: ScheduledTask) => void;
  /** Overrides the plain status name, e.g. "Stopped at step 6". */
  statusLabel?: string | null;
}

/** "Scheduled / <task> ⌄" above a run's conversation
 * (docs/ui-references/scheduled-siderbar-task-running.png). The task name
 * opens a menu to the task's page and its other runs, so moving between
 * runs never needs a trip back to the portal. */
export function RunBreadcrumb({ task, threadId, onOpenPortal, onOpenTask, statusLabel }: RunBreadcrumbProps) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useClickOutside(menuRef, () => setOpen(false), open);

  const currentRun = task?.runs.find((r) => r.thread_id === threadId) ?? null;
  const runs = task ? [...task.runs].reverse().slice(0, MAX_RUNS_LISTED) : [];
  const showStatus = currentRun !== null && currentRun.status !== "completed";

  return (
    <div className="flex min-w-0 flex-1 items-center gap-1.5 text-sm">
      <button
        type="button"
        className="flex shrink-0 items-center gap-1.5 rounded-md px-1.5 py-1 text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
        onClick={onOpenPortal}
      >
        <ClockIcon className="h-4 w-4" />
        Scheduled
      </button>
      <span className="text-[var(--muted)]">/</span>
      <div className="relative min-w-0" ref={menuRef}>
        <button
          type="button"
          disabled={!task}
          aria-haspopup="menu"
          aria-expanded={open}
          className="flex min-w-0 items-center gap-1 rounded-md px-1.5 py-1 font-medium hover:bg-[var(--card-bg)] disabled:hover:bg-transparent"
          onClick={() => setOpen((v) => !v)}
        >
          <span className="truncate">{task?.name ?? "…"}</span>
          <ChevronDownIcon className="h-3.5 w-3.5 shrink-0 text-[var(--muted)]" />
        </button>
        {open && task && (
          <div
            role="menu"
            className="absolute left-0 top-full z-20 mt-1 w-72 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] py-1 shadow-[var(--shadow)]"
          >
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-[var(--card-bg)]"
              onClick={() => {
                setOpen(false);
                onOpenTask(task);
              }}
            >
              Task details
              <ChevronRightIcon className="h-4 w-4 text-[var(--muted)]" />
            </button>
            <div className="my-1 border-t border-[var(--border)]" />
            <div className="px-3 pb-1 pt-1.5 text-xs font-medium tracking-wide text-[var(--muted)]">RUNS</div>
            <div className="max-h-72 overflow-y-auto">
              {runs.map((run) => {
                const isCurrent = run.thread_id === threadId;
                return (
                  <button
                    key={run.run_id}
                    type="button"
                    role="menuitem"
                    aria-current={isCurrent || undefined}
                    className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]"
                    onClick={() => {
                      setOpen(false);
                      if (!isCurrent) goToThread(run.thread_id);
                    }}
                  >
                    <RunStatusIcon status={run.status} />
                    <span className="min-w-0 flex-1 truncate">
                      {capitalize(formatRunTime(run.started_at))}
                      <span className="text-[var(--muted)]"> · {runSourceLabel(run)}</span>
                    </span>
                    {isCurrent && <CheckIcon className="h-3.5 w-3.5 shrink-0 text-[var(--muted)]" />}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>
      {showStatus && currentRun && (
        <span className="flex shrink-0 items-center gap-1.5 rounded-full border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--muted)]">
          <RunStatusIcon status={currentRun.status} className="h-3 w-3" />
          {statusLabel ?? RUN_STATUS_LABEL[currentRun.status]}
        </span>
      )}
    </div>
  );
}
