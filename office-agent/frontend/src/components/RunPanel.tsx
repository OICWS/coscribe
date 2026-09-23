import { useEffect, useRef, useState, type MouseEvent } from "react";
import { deleteScheduledTask, getScheduledTasks, pauseScheduledTask, resumeScheduledTask, runScheduledTaskNow } from "../lib/rest";
import { useClickOutside } from "../lib/useClickOutside";
import { goToThread } from "../lib/nav";
import { describeSchedule } from "../lib/scheduleLabels";
import type { ScheduledTask } from "../types/settings";
import { ScheduledTaskDetail } from "./ScheduledTaskDetail";
import { ChevronDownIcon, MoreIcon, PauseIcon, PencilIcon, PlayIcon, SearchIcon, TrashIcon } from "./icons";
import { ConfirmDialog } from "./ConfirmDialog";

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

/** Scheduled mode's main content: the card-grid portal
 * (docs/ui-references/sheduled-main-portal.png), or ScheduledTaskDetail
 * for whichever task is selected. Selection lives in App.tsx since a
 * sidebar row (NavRail) and a card here both drive it. */
export function RunPanel({
  refreshKey,
  scheduledTasksVersion,
  onScheduledTasksChanged,
  selectedScheduledTask,
  onSelectScheduledTask,
  onOpenScheduledTask,
  onEditScheduledTask,
}: RunPanelProps) {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [newTaskMenuOpen, setNewTaskMenuOpen] = useState(false);
  const newTaskMenuRef = useRef<HTMLDivElement>(null);
  useClickOutside(newTaskMenuRef, () => setNewTaskMenuOpen(false), newTaskMenuOpen);

  const refresh = () => {
    getScheduledTasks().then(setTasks);
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

  if (selectedScheduledTask) {
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
                  {task.prompt}
                </p>
                <div className="mt-3">
                  <span className="inline-block rounded-md bg-green-500/15 px-2 py-1 text-xs font-medium text-green-700 dark:text-green-400">
                    {describeSchedule(task.schedule)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
    </div>
  );
}
