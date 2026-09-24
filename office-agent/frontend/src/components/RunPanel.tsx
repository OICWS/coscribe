import { useRef, useState, type MouseEvent } from "react";
import { deleteScheduledTask, pauseScheduledTask, resumeScheduledTask } from "../lib/rest";
import { useClickOutside } from "../lib/useClickOutside";
import { capitalize, formatRunTime, latestRun, RUN_STATUS_LABEL } from "../lib/runLabels";
import { describeSchedule } from "../lib/scheduleLabels";
import type { ScheduledTask } from "../types/settings";
import { ScheduledTaskDetail } from "./ScheduledTaskDetail";
import { ChevronDownIcon, MoreIcon, PauseIcon, PencilIcon, PlayIcon, SearchIcon, TrashIcon } from "./icons";
import { ConfirmDialog } from "./ConfirmDialog";
import { RunStatusIcon } from "./RunStatusIcon";

interface TaskCardMenuProps {
  task: ScheduledTask;
  onEdit: () => void;
  onRunNow: () => void;
  onChanged: () => void;
}

/** The hover "..." menu on a portal card -- Run now/Pause-Resume/Edit/
 * Delete (the sidebar's own menu, a separate component, drops Pause --
 * see sheduled-siderbar-workflow-display-settings.png). */
function TaskCardMenu({ task, onEdit, onRunNow, onChanged }: TaskCardMenuProps) {
  const [open, setOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useClickOutside(menuRef, () => setOpen(false), open);

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

  const item = "flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]";
  return (
    <div className="relative shrink-0" ref={menuRef}>
      <button
        type="button"
        aria-label={`Options for ${task.name}`}
        className={`flex h-7 w-7 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--border)] ${
          open ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
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
          <button
            type="button"
            className={item}
            onClick={() => {
              setOpen(false);
              onRunNow();
            }}
          >
            <PlayIcon className="h-3.5 w-3.5" /> Run now
          </button>
          <button type="button" className={item} onClick={togglePause}>
            <PauseIcon className="h-3.5 w-3.5" /> {task.enabled ? "Pause" : "Resume"}
          </button>
          <button
            type="button"
            className={item}
            onClick={() => {
              setOpen(false);
              onEdit();
            }}
          >
            <PencilIcon className="h-3.5 w-3.5" /> Edit
          </button>
          <div className="my-1 border-t border-[var(--border)]" />
          <button
            type="button"
            className={`${item} text-[var(--danger)]`}
            onClick={() => {
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
          description={`"${task.name}" and the conversations of its runs will be permanently removed.`}
          onCancel={() => setDeleteConfirm(false)}
          onConfirm={confirmDelete}
        />
      )}
    </div>
  );
}

function LastRunLine({ task }: { task: ScheduledTask }) {
  const run = latestRun(task);
  if (!run) return <span className="text-xs text-[var(--muted)]">Not run yet</span>;
  const label =
    run.status === "running"
      ? "Running now"
      : run.status === "completed"
        ? `Ran ${formatRunTime(run.started_at)}`
        : `${RUN_STATUS_LABEL[run.status]} · ${formatRunTime(run.started_at)}`;
  return (
    <span className="flex min-w-0 items-center gap-1.5 text-xs text-[var(--muted)]">
      <RunStatusIcon status={run.status} className="h-3 w-3" />
      <span className="truncate">{capitalize(label)}</span>
    </span>
  );
}

interface RunPanelProps {
  tasks: ScheduledTask[];
  onScheduledTasksChanged: () => void;
  selectedTask: ScheduledTask | null;
  onSelectTask: (task: ScheduledTask | null) => void;
  onOpenTask: (task: ScheduledTask) => void;
  onEditTask: (task: ScheduledTask | null) => void;
  onRunNow: (task: ScheduledTask, inputs?: Record<string, unknown>) => void;
  onCreateWithCoscribe: () => void;
  focusStepId?: string | null;
}

/** Scheduled mode's main content: the card-grid portal
 * (docs/ui-references/sheduled-main-portal.png), or ScheduledTaskDetail
 * for whichever task is selected. Selection lives in App.tsx since a
 * sidebar row (NavRail) and a card here both drive it. */
export function RunPanel({
  tasks,
  onScheduledTasksChanged,
  selectedTask,
  onSelectTask,
  onOpenTask,
  onEditTask,
  onRunNow,
  onCreateWithCoscribe,
  focusStepId,
}: RunPanelProps) {
  const [newTaskMenuOpen, setNewTaskMenuOpen] = useState(false);
  const newTaskMenuRef = useRef<HTMLDivElement>(null);
  useClickOutside(newTaskMenuRef, () => setNewTaskMenuOpen(false), newTaskMenuOpen);

  if (selectedTask) {
    return (
      <ScheduledTaskDetail
        key={selectedTask.trigger_id}
        task={selectedTask}
        onEdit={() => onEditTask(selectedTask)}
        onRunNow={(inputs) => onRunNow(selectedTask, inputs)}
        focusStepId={focusStepId}
        onDeleted={() => {
          onSelectTask(null);
          onScheduledTasksChanged();
        }}
        onChanged={onScheduledTasksChanged}
      />
    );
  }

  const menuItem = "flex w-full flex-col items-start px-3 py-2 text-left hover:bg-[var(--card-bg)]";
  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-5xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold" style={{ fontFamily: "Georgia, 'Times New Roman', serif" }}>
              Scheduled tasks
            </h1>
            <p className="mt-1 text-sm text-[var(--muted)]">Run tasks on a schedule or whenever you need them.</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
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
              Sort by <span className="font-medium text-[var(--fg)]">Next run</span>{" "}
              <ChevronDownIcon className="h-3.5 w-3.5" />
            </button>
            <div className="relative" ref={newTaskMenuRef}>
              <button
                type="button"
                aria-expanded={newTaskMenuOpen}
                className="flex items-center gap-1 rounded-md bg-[var(--primary)] px-3 py-1.5 text-sm font-medium text-[var(--primary-fg)] hover:bg-[var(--primary-hover)]"
                onClick={() => setNewTaskMenuOpen((v) => !v)}
              >
                New task <ChevronDownIcon className="h-3.5 w-3.5" />
              </button>
              {newTaskMenuOpen && (
                <div className="absolute right-0 top-full z-10 mt-1 w-64 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] py-1 shadow-[var(--shadow)]">
                  <button
                    type="button"
                    className={menuItem}
                    onClick={() => {
                      setNewTaskMenuOpen(false);
                      onCreateWithCoscribe();
                    }}
                  >
                    <span className="text-sm">Create with coscribe</span>
                    <span className="text-xs text-[var(--muted)]">Describe it in a chat, or walk through it once</span>
                  </button>
                  <button
                    type="button"
                    className={menuItem}
                    onClick={() => {
                      setNewTaskMenuOpen(false);
                      onEditTask(null);
                    }}
                  >
                    <span className="text-sm">Set up manually</span>
                    <span className="text-xs text-[var(--muted)]">Write the instructions and schedule yourself</span>
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {tasks.length === 0 ? (
          <div className="mt-10 rounded-xl border border-dashed border-[var(--border)] px-6 py-10 text-center">
            <p className="font-medium">No scheduled tasks yet</p>
            <p className="mx-auto mt-1 max-w-md text-sm text-[var(--muted)]">
              Do something once in a chat, then ask coscribe to save it as a task -- or type
              <code className="mx-1 rounded bg-[var(--code-bg)] px-1">/saveworkflow &lt;name&gt;</code>.
            </p>
            <button
              type="button"
              className="mt-4 rounded-md border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--card-bg)]"
              onClick={onCreateWithCoscribe}
            >
              Create with coscribe
            </button>
          </div>
        ) : (
          <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
            {tasks.map((task) => (
              <div
                key={task.trigger_id}
                role="button"
                tabIndex={0}
                className="group flex cursor-pointer flex-col rounded-lg border border-[var(--border)] p-4 outline-none hover:border-[var(--border-hover)] focus-visible:border-[var(--accent)]"
                onClick={() => onOpenTask(task)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onOpenTask(task);
                  }
                }}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 truncate font-medium">{task.name}</div>
                  <TaskCardMenu
                    task={task}
                    onEdit={() => onEditTask(task)}
                    onRunNow={() => onRunNow(task)}
                    onChanged={onScheduledTasksChanged}
                  />
                </div>
                <p className="mt-1 line-clamp-2 text-sm text-[var(--muted)]">
                  {task.workflow
                    ? `Workflow · ${task.workflow.steps.map((step) => step.title).join(" → ")}`
                    : task.prompt}
                </p>
                <div className="mt-3 flex items-center justify-between gap-3">
                  <span
                    className={`inline-block shrink-0 rounded-md px-2 py-1 text-xs font-medium ${
                      task.enabled
                        ? "bg-green-500/15 text-green-700 dark:text-green-400"
                        : "bg-[var(--card-bg)] text-[var(--muted)]"
                    }`}
                  >
                    {task.enabled ? describeSchedule(task.schedule) : "Paused"}
                  </span>
                  <LastRunLine task={task} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
