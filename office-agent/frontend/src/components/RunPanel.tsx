import { useMemo, useRef, useState, type MouseEvent } from "react";
import { deleteScheduledTask, pauseScheduledTask, resumeScheduledTask } from "../lib/rest";
import { useClickOutside } from "../lib/useClickOutside";
import { capitalize, formatRunTime, latestRun, RUN_STATUS_LABEL } from "../lib/runLabels";
import { describeSchedule } from "../lib/scheduleLabels";
import type { ScheduledTask } from "../types/settings";
import { ScheduledTaskDetail } from "./ScheduledTaskDetail";
import {
  CheckIcon,
  ChevronDownIcon,
  CloseIcon,
  MoreIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  SearchIcon,
  TrashIcon,
} from "./icons";
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

type SortKey = "next" | "name";

const SORT_LABEL: Record<SortKey, string> = { next: "Next run", name: "Name" };
const SORT_STORAGE_KEY = "coscribe.scheduledSort";

function storedSort(): SortKey {
  try {
    return localStorage.getItem(SORT_STORAGE_KEY) === "name" ? "name" : "next";
  } catch {
    return "next";
  }
}

function byName(a: ScheduledTask, b: ScheduledTask): number {
  return a.name.localeCompare(b.name, undefined, { sensitivity: "base", numeric: true });
}

/** Soonest first; tasks with no next run (paused or run by hand) after
 * every scheduled one, by name. */
function byNextRun(a: ScheduledTask, b: ScheduledTask): number {
  if (a.next_run_at && b.next_run_at) return a.next_run_at.localeCompare(b.next_run_at) || byName(a, b);
  if (a.next_run_at) return -1;
  if (b.next_run_at) return 1;
  return byName(a, b);
}

function matchesQuery(task: ScheduledTask, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  const haystack = [task.name, task.prompt, ...(task.workflow?.steps.map((step) => step.title) ?? [])];
  return haystack.some((text) => text?.toLowerCase().includes(needle));
}

interface RunPanelProps {
  tasks: ScheduledTask[];
  onScheduledTasksChanged: () => void;
  selectedTask: ScheduledTask | null;
  onSelectTask: (task: ScheduledTask | null) => void;
  onOpenTask: (task: ScheduledTask) => void;
  onEditTask: (task: ScheduledTask | null) => void;
  onBuildWorkflow: () => void;
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
  onBuildWorkflow,
  onRunNow,
  onCreateWithCoscribe,
  focusStepId,
}: RunPanelProps) {
  const [newTaskMenuOpen, setNewTaskMenuOpen] = useState(false);
  const newTaskMenuRef = useRef<HTMLDivElement>(null);
  useClickOutside(newTaskMenuRef, () => setNewTaskMenuOpen(false), newTaskMenuOpen);
  const [sortMenuOpen, setSortMenuOpen] = useState(false);
  const sortMenuRef = useRef<HTMLDivElement>(null);
  useClickOutside(sortMenuRef, () => setSortMenuOpen(false), sortMenuOpen);
  const [sort, setSort] = useState<SortKey>(storedSort);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");

  const shownTasks = useMemo(
    () => tasks.filter((task) => matchesQuery(task, query)).sort(sort === "name" ? byName : byNextRun),
    [tasks, query, sort],
  );

  const chooseSort = (key: SortKey) => {
    setSort(key);
    setSortMenuOpen(false);
    try {
      localStorage.setItem(SORT_STORAGE_KEY, key);
    } catch {
      // Unavailable storage only costs remembering the choice.
    }
  };

  const closeSearch = () => {
    setSearchOpen(false);
    setQuery("");
  };

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
            {searchOpen ? (
              <div className="flex h-9 w-56 items-center gap-1.5 rounded-md border border-[var(--border)] px-2.5 focus-within:border-[var(--border-hover)]">
                <SearchIcon className="h-4 w-4 shrink-0 text-[var(--muted)]" />
                <input
                  autoFocus
                  aria-label="Search tasks"
                  placeholder="Search tasks"
                  className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--muted)]"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => e.key === "Escape" && closeSearch()}
                />
                <button
                  type="button"
                  aria-label="Close search"
                  className="shrink-0 rounded text-[var(--muted)] hover:text-[var(--fg)]"
                  onClick={closeSearch}
                >
                  <CloseIcon className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : (
              <button
                type="button"
                aria-label="Search"
                title="Search"
                className="flex h-9 w-9 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
                onClick={() => setSearchOpen(true)}
              >
                <SearchIcon className="h-4 w-4" />
              </button>
            )}
            <div className="relative" ref={sortMenuRef}>
              <button
                type="button"
                aria-haspopup="menu"
                aria-expanded={sortMenuOpen}
                className={`flex h-9 items-center gap-1 rounded-md border border-[var(--border)] px-3 text-sm text-[var(--muted)] hover:bg-[var(--card-bg)] ${sortMenuOpen ? "bg-[var(--card-bg)]" : ""}`}
                onClick={() => setSortMenuOpen((v) => !v)}
              >
                Sort by <span className="font-medium text-[var(--fg)]">{SORT_LABEL[sort]}</span>{" "}
                <ChevronDownIcon className="h-3.5 w-3.5" />
              </button>
              {sortMenuOpen && (
                <div
                  role="menu"
                  className="absolute right-0 top-full z-10 mt-1 w-40 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] p-1 shadow-[var(--shadow)]"
                >
                  {(Object.keys(SORT_LABEL) as SortKey[]).map((key) => (
                    <button
                      key={key}
                      type="button"
                      role="menuitemradio"
                      aria-checked={sort === key}
                      className="flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]"
                      onClick={() => chooseSort(key)}
                    >
                      {SORT_LABEL[key]}
                      {sort === key && <CheckIcon className="h-4 w-4 text-[var(--accent)]" />}
                    </button>
                  ))}
                </div>
              )}
            </div>
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
                  <button
                    type="button"
                    className={menuItem}
                    onClick={() => {
                      setNewTaskMenuOpen(false);
                      onBuildWorkflow();
                    }}
                  >
                    <span className="text-sm">Build a workflow</span>
                    <span className="text-xs text-[var(--muted)]">Fixed steps you control, checked as they run</span>
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
              Do something once in a chat, then turn it into fixed steps with
              <code className="mx-1 rounded bg-[var(--code-bg)] px-1">/saveworkflow</code>
              -- or ask coscribe to save it as a task.
            </p>
            <button
              type="button"
              className="mt-4 rounded-md border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--card-bg)]"
              onClick={onCreateWithCoscribe}
            >
              Create with coscribe
            </button>
          </div>
        ) : shownTasks.length === 0 ? (
          <p className="mt-10 text-center text-sm text-[var(--muted)]">No tasks match &ldquo;{query.trim()}&rdquo;</p>
        ) : (
          <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
            {shownTasks.map((task) => (
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
                    ? `Workflow · ${task.workflow.steps.map((step) => step.title).join(" → ") || "no steps yet"}`
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
