import { useEffect, useRef, useState, type MouseEvent } from "react";
import { deleteScheduledTask, deleteWorkflowRun, getScheduledTasks, getWorkflowRuns, getWorkflows, pauseScheduledTask, resumeScheduledTask, runScheduledTaskNow } from "../lib/rest";
import { useClickOutside } from "../lib/useClickOutside";
import { goToThread } from "../lib/nav";
import { describeSchedule } from "../lib/scheduleLabels";
import type { ScheduledTask, Workflow, WorkflowRun, WorkflowRunStepStatus } from "../types/settings";
import { ScheduledTaskDetail } from "./ScheduledTaskDetail";
import { CheckCircleIcon, ChevronDownIcon, ClockIcon, MoreIcon, PauseIcon, PencilIcon, PlayIcon, SearchIcon, TrashIcon, XCircleIcon, ZapIcon } from "./icons";
import type { RunTab } from "./NavRail";
import { ConfirmDialog } from "./ConfirmDialog";

const STEP_STATUS_ICON: Record<WorkflowRunStepStatus["status"], string> = {
  pending: "·",
  running: "…",
  done: "✓",
  failed: "×",
  stopped: "■",
};

function RunStatusIcon({ status }: { status: WorkflowRun["status"] }) {
  const className = "h-4 w-4 shrink-0";
  if (status === "completed") return <CheckCircleIcon className={`${className} text-[var(--accent)]`} />;
  if (status === "running") return <ClockIcon className={`${className} text-[var(--muted)]`} />;
  return <XCircleIcon className={`${className} text-[var(--danger)]`} />;
}

interface TaskCardMenuProps {
  task: ScheduledTask;
  onEdit: () => void;
  onChanged: () => void;
}

/** The hover "..." menu on a portal card -- Run now/Pause-Resume/Edit/
 * Delete, matching the first user-pasted screenshot's card menu (Run
 * now/Pause/Edit/Delete; the sidebar's own menu, a separate component,
 * drops Pause -- see sheduled-siderbar-workflow-display-settings.png). */
function TaskCardMenu({ task, onEdit, onChanged }: TaskCardMenuProps) {
  const [open, setOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useClickOutside(menuRef, () => setOpen(false), open);

  const runNow = async (e: MouseEvent) => {
    e.stopPropagation();
    setOpen(false);
    await runScheduledTaskNow(task.trigger_id);
    onChanged();
    goToThread(task.thread_id);
  };

  const togglePause = async (e: MouseEvent) => {
    e.stopPropagation();
    setOpen(false);
    if (task.enabled) await pauseScheduledTask(task.trigger_id);
    else await resumeScheduledTask(task.trigger_id);
    onChanged();
  };

  const confirmDelete = async () => {
    setDeleteConfirm(false);
    await deleteScheduledTask(task.trigger_id);
    onChanged();
  };

  return (
    <div className="relative shrink-0" ref={menuRef}>
      <button
        type="button"
        aria-label={`Options for ${task.name}`}
        className={`flex h-7 w-7 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--border)] ${
          open ? "opacity-100" : "opacity-0 group-hover:opacity-100"
        }`}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        <MoreIcon className="h-4 w-4" />
      </button>
      {open && (
        <div
          className="absolute right-0 top-full z-10 mt-1 min-w-36 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] py-1 shadow-[var(--shadow)]"
          onClick={(e) => e.stopPropagation()}
        >
          <button type="button" className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]" onClick={runNow}>
            <PlayIcon className="h-3.5 w-3.5" /> Run now
          </button>
          <button type="button" className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]" onClick={togglePause}>
            <PauseIcon className="h-3.5 w-3.5" /> {task.enabled ? "Pause" : "Resume"}
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]"
            onClick={(e) => {
              e.stopPropagation();
              setOpen(false);
              onEdit();
            }}
          >
            <PencilIcon className="h-3.5 w-3.5" /> Edit
          </button>
          <div className="my-1 border-t border-[var(--border)]" />
          <button
            type="button"
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-[var(--danger)] hover:bg-[var(--card-bg)]"
            onClick={(e) => {
              e.stopPropagation();
              setOpen(false);
              setDeleteConfirm(true);
            }}
          >
            <TrashIcon className="h-3.5 w-3.5" /> Delete
          </button>
        </div>
      )}
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

interface RunPanelProps {
  runTab: RunTab;
  onRunWorkflow: (name: string) => void;
  refreshKey: number;
  scheduledTasksVersion: number;
  onScheduledTasksChanged: () => void;
  selectedScheduledTask: ScheduledTask | null;
  // Plain state sync (the auto-refresh effect below, and clearing
  // selection when a run navigates away) vs. the "smart open" a card
  // click gets (goToThread if the task has already run) -- see
  // App.tsx's openScheduledTask and NavRail.tsx's identical split.
  onSelectScheduledTask: (task: ScheduledTask | null) => void;
  onOpenScheduledTask: (task: ScheduledTask) => void;
  onEditScheduledTask: (task: ScheduledTask | null) => void;
}

/** The Run mode's main content. runTab === "workflows" is unrelated to
 * Scheduled Tasks -- it's SessionMenu.tsx's old saved-Workflow-definition
 * list (chain/agent-mode macros you replay by name), left as-is here.
 * runTab === "scheduled" is the redesigned Scheduled Tasks surface: a
 * card-grid portal (docs/ui-references/sheduled-main-portal.png) by
 * default, or ScheduledTaskDetail for whichever task is selected (from a
 * card click here or a sidebar row click in NavRail -- selection state
 * lives in App.tsx since both components need to drive it). The old
 * inline create form is gone, replaced by ScheduledTaskModal (also
 * App-level, so it can be opened from NavRail's sidebar menu too). */
export function RunPanel({
  runTab,
  onRunWorkflow,
  refreshKey,
  scheduledTasksVersion,
  onScheduledTasksChanged,
  selectedScheduledTask,
  onSelectScheduledTask,
  onOpenScheduledTask,
  onEditScheduledTask,
}: RunPanelProps) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [newTaskMenuOpen, setNewTaskMenuOpen] = useState(false);
  const newTaskMenuRef = useRef<HTMLDivElement>(null);
  useClickOutside(newTaskMenuRef, () => setNewTaskMenuOpen(false), newTaskMenuOpen);

  const refresh = () => {
    getWorkflows().then(setWorkflows);
    getScheduledTasks().then(setTasks);
    getWorkflowRuns().then(setRuns);
  };

  useEffect(refresh, [refreshKey, scheduledTasksVersion]);

  // The currently-selected task's own object can go stale the moment
  // something elsewhere changes it (e.g. NavRail's sidebar menu pausing
  // it) -- refresh from the freshly-fetched list rather than trusting the
  // possibly-stale prop, so ScheduledTaskDetail's toggle/next-run always
  // reflect the latest save.
  useEffect(() => {
    if (!selectedScheduledTask) return;
    const fresh = tasks.find((t) => t.trigger_id === selectedScheduledTask.trigger_id);
    if (fresh && fresh !== selectedScheduledTask) onSelectScheduledTask(fresh);
    if (!fresh) onSelectScheduledTask(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks]);

  const removeRun = (runId: string) => {
    if (!window.confirm("Delete this run record?")) return;
    deleteWorkflowRun(runId).then(refresh);
  };

  if (runTab === "scheduled" && selectedScheduledTask) {
    return (
      <ScheduledTaskDetail
        task={selectedScheduledTask}
        onEdit={() => onEditScheduledTask(selectedScheduledTask)}
        onDeleted={() => {
          onSelectScheduledTask(null);
          onScheduledTasksChanged();
        }}
        onChanged={onScheduledTasksChanged}
      />
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      {runTab === "workflows" && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {workflows.length === 0 && <div className="text-sm text-[var(--muted)]">No workflows saved yet.</div>}
          {workflows.map((wf) => (
            <div key={wf.name} className="rounded-lg bg-[var(--card-bg)] p-4">
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-[var(--accent)]">Workflow</div>
              <div className="mb-1 font-semibold">{wf.name}</div>
              <p className="mb-3 text-sm text-[var(--muted)]">{wf.summary}</p>
              <button
                type="button"
                className="flex items-center gap-1.5 rounded-md border border-[var(--border)] px-3 py-1.5 text-sm font-medium hover:bg-[var(--panel-bg)]"
                onClick={() => onRunWorkflow(wf.name)}
              >
                <ZapIcon className="h-3.5 w-3.5" /> Run now
              </button>
            </div>
          ))}
        </div>
      )}

      {runTab === "scheduled" && (
        <div className="mx-auto max-w-5xl">
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-2xl font-semibold" style={{ fontFamily: "Georgia, 'Times New Roman', serif" }}>
                Scheduled tasks
              </h1>
              <p className="mt-1 text-sm text-[var(--muted)]">Run tasks on a schedule or whenever you need them.</p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                aria-label="Search"
                className="flex h-9 w-9 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)]"
              >
                <SearchIcon className="h-4 w-4" />
              </button>
              <button
                type="button"
                className="flex items-center gap-1 rounded-md border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--muted)]"
              >
                Sort by <span className="font-medium text-[var(--fg)]">Next run</span> <ChevronDownIcon className="h-3.5 w-3.5" />
              </button>
              <div className="relative" ref={newTaskMenuRef}>
                <button
                  type="button"
                  className="flex items-center gap-1 rounded-md bg-[var(--primary)] px-3 py-1.5 text-sm font-medium text-[var(--primary-fg)]"
                  onClick={() => setNewTaskMenuOpen((v) => !v)}
                >
                  New task <ChevronDownIcon className="h-3.5 w-3.5" />
                </button>
                {newTaskMenuOpen && (
                  <div className="absolute right-0 top-full z-10 mt-1 min-w-44 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] py-1 shadow-[var(--shadow)]">
                    <button
                      type="button"
                      className="flex w-full items-center px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]"
                      onClick={() => {
                        setNewTaskMenuOpen(false);
                        onEditScheduledTask(null);
                      }}
                    >
                      Set up manually
                    </button>
                    <button
                      type="button"
                      disabled
                      title="Not available yet"
                      className="flex w-full items-center px-3 py-1.5 text-left text-sm text-[var(--muted)] opacity-60"
                    >
                      Create with coscribe
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
            {tasks.length === 0 && <div className="text-sm text-[var(--muted)]">No scheduled tasks yet.</div>}
            {tasks.map((task) => (
              <div
                key={task.trigger_id}
                className="group cursor-pointer rounded-lg border border-[var(--border)] p-4 hover:border-[var(--muted)]"
                onClick={() => onOpenScheduledTask(task)}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="font-medium">{task.name}</div>
                  <TaskCardMenu task={task} onEdit={() => onEditScheduledTask(task)} onChanged={onScheduledTasksChanged} />
                </div>
                <p className="mt-1 line-clamp-2 text-sm text-[var(--muted)]">
                  {task.workflow_name ? `Runs workflow: ${task.workflow_name}` : task.prompt}
                </p>
                <div className="mt-3">
                  <span className="inline-block rounded-md bg-green-500/15 px-2 py-1 text-xs font-medium text-green-700 dark:text-green-400">
                    {describeSchedule(task.schedule)}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {/* "History" folded into "Scheduled" -- see NavRail.tsx's own
           * RunTab comment; both are "things that ran without you typing
           * a message right now." */}
          <div className="mt-8 border-t border-[var(--border)] pt-4 text-sm font-medium uppercase tracking-wide text-[var(--muted)]">
            Recent runs
          </div>
          {runs.length === 0 && <div className="py-2 text-sm text-[var(--muted)]">No runs yet.</div>}
          {runs.map((run) => (
            <div key={run.run_id} className="flex items-start justify-between gap-3 border-b border-[var(--border)] py-3">
              <div className="flex min-w-0 items-start gap-2">
                <RunStatusIcon status={run.status} />
                <div className="min-w-0">
                  <div className="font-medium">{run.workflow_name}</div>
                  <div className="text-sm text-[var(--muted)]">
                    {run.finished_at
                      ? new Date(run.finished_at).toLocaleString()
                      : `Started ${new Date(run.started_at).toLocaleString()}`}
                  </div>
                  {run.error && <div className="text-sm text-[var(--danger)]">{run.error}</div>}
                  {run.steps.length > 0 && (
                    <div className="mt-1 flex flex-wrap items-center gap-1">
                      {run.steps.map((step) => (
                        <span
                          key={step.index}
                          title={step.detail ?? undefined}
                          className="rounded-md border border-[var(--border)] px-1.5 py-0.5 text-xs"
                        >
                          {STEP_STATUS_ICON[step.status]} {step.tool_name}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {run.steps.length > 0 && (
                  <span className="rounded-md border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--muted)]">
                    {run.steps.length} {run.steps.length === 1 ? "step" : "steps"}
                  </span>
                )}
                {run.status !== "running" && (
                  <button
                    type="button"
                    aria-label="Delete run record"
                    className="text-[var(--muted)] hover:text-[var(--danger)]"
                    onClick={() => removeRun(run.run_id)}
                  >
                    &times;
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
