import { useEffect, useRef, useState } from "react";
import {
  deleteScheduledTask,
  deleteThread,
  deleteWorkflowRun,
  getScheduledTasks,
  getThreads,
  renameThread,
  runScheduledTaskNow,
} from "../lib/rest";
import { useClickOutside } from "../lib/useClickOutside";
import { goToThread } from "../lib/nav";
import { scheduleKindLabel } from "../lib/scheduleLabels";
import type { ScheduledTask } from "../types/settings";
import type { ThreadSummary } from "../types/session";
import type { WorkflowRun } from "../types/wire";
import { ConfirmDialog } from "./ConfirmDialog";
import {
  ClockIcon,
  MessageCircleIcon,
  MoreIcon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  SidebarIcon,
  TrashIcon,
} from "./icons";

export type NavMode = "create" | "run";
// "history" folded into "scheduled" -- both are "things that ran without
// you typing a message right now," and the flat New/Scheduled nav shape
// (see NavRail's own docstring below) has no third slot for it.
export type RunTab = "workflows" | "scheduled";

function startNewSession() {
  window.location.href = window.location.pathname;
}

interface ThreadRowProps {
  thread: ThreadSummary;
  isCurrent: boolean;
  onRenamed: (title: string) => void;
  onDeleteRequest: () => void;
}

/** One session row: click to switch threads, a "..." menu (Rename/Delete)
 * that only shows on hover or while open -- replaces the old lone hover-
 * to-reveal "x" delete button, which had no rename at all and used the
 * browser's own window.confirm() for delete (see NavRail's removeThread,
 * now superseded by App-level deleteTarget + a real confirm dialog
 * matching this app's modal styling instead of an OS-chrome popup). */
function ThreadRow({ thread, isCurrent, onRenamed, onDeleteRequest }: ThreadRowProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [value, setValue] = useState(thread.preview);
  const menuRef = useRef<HTMLDivElement>(null);
  useClickOutside(menuRef, () => setMenuOpen(false), menuOpen);

  const commitRename = () => {
    setRenaming(false);
    const title = value.trim();
    if (!title || title === thread.preview) {
      setValue(thread.preview);
      return;
    }
    renameThread(thread.thread_id, title).then((result) => {
      if ("title" in result) onRenamed(result.title);
      else setValue(thread.preview);
    });
  };

  if (renaming) {
    return (
      <input
        autoFocus
        className="w-full min-w-0 rounded-md border border-[var(--accent)] bg-[var(--card-bg)] px-2 py-1 text-sm outline-none"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commitRename}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commitRename();
          } else if (e.key === "Escape") {
            setValue(thread.preview);
            setRenaming(false);
          }
        }}
      />
    );
  }

  return (
    <div
      className={`group flex min-w-0 items-center justify-between gap-1 rounded-md px-2 py-1.5 text-sm hover:bg-[var(--card-bg)] ${
        isCurrent ? "font-medium" : "cursor-pointer"
      }`}
      onClick={() => !isCurrent && goToThread(thread.thread_id)}
    >
      <span className="min-w-0 truncate" title={thread.preview || thread.thread_id}>
        {thread.preview || thread.thread_id}
      </span>
      <div className="relative shrink-0" ref={menuRef}>
        <button
          type="button"
          aria-label={`Options for ${thread.preview || thread.thread_id}`}
          className={`rounded-md p-1 text-[var(--muted)] hover:bg-[var(--border)] ${
            menuOpen ? "opacity-100" : "opacity-0 group-hover:opacity-100"
          }`}
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen((v) => !v);
          }}
        >
          <MoreIcon className="h-3.5 w-3.5" />
        </button>
        {menuOpen && (
          <div className="absolute right-0 top-full z-10 mt-1 min-w-36 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] py-1 shadow-[var(--shadow)]">
            <button
              type="button"
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]"
              onClick={(e) => {
                e.stopPropagation();
                setMenuOpen(false);
                setValue(thread.preview);
                setRenaming(true);
              }}
            >
              <PencilIcon className="h-3.5 w-3.5" /> Rename
            </button>
            {!isCurrent && (
              <button
                type="button"
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-red-500 hover:bg-[var(--card-bg)]"
                onClick={(e) => {
                  e.stopPropagation();
                  setMenuOpen(false);
                  onDeleteRequest();
                }}
              >
                <TrashIcon className="h-3.5 w-3.5" /> Delete
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

interface ScheduledTaskRowProps {
  task: ScheduledTask;
  onSelect: () => void;
  onEdit: () => void;
  onChanged: () => void;
}

/** One Scheduled sidebar row -- matches
 * docs/ui-references/sheduled-sidebar-workflow-display.png: a leading
 * bullet, the name, and (until hovered) the schedule kind right-aligned
 * in muted text; hovering swaps that label for a "..." menu (Run now/
 * Edit/Delete -- no Pause, see sheduled-siderbar-workflow-display-
 * settings.png, unlike the portal card's own menu which keeps it).
 * Clicking the row itself opens ScheduledTaskDetail in RunPanel's main
 * area, one of the three confirmed entry points into the Edit modal
 * (via this row's own "Edit" item, or the detail page's pencil icon). */
function ScheduledTaskRow({ task, onSelect, onEdit, onChanged }: ScheduledTaskRowProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useClickOutside(menuRef, () => setMenuOpen(false), menuOpen);

  const runNow = async () => {
    setMenuOpen(false);
    await runScheduledTaskNow(task.trigger_id);
    // Land on the fired trigger's own conversation -- see goToThread's
    // own docstring for why "just show a status and make the user go
    // find it in chat" isn't good enough here.
    goToThread(task.thread_id);
  };

  const confirmDelete = async () => {
    setDeleteConfirm(false);
    await deleteScheduledTask(task.trigger_id);
    onChanged();
  };

  return (
    <div
      className="group flex min-w-0 items-center gap-1.5 rounded-md px-2 py-1.5 text-sm hover:bg-[var(--card-bg)]"
      onClick={onSelect}
    >
      <span className="shrink-0 text-[var(--muted)]">○</span>
      <span className="min-w-0 flex-1 truncate" title={task.name}>
        {task.name}
      </span>
      <span className="shrink-0 text-xs text-[var(--muted)] group-hover:hidden">{scheduleKindLabel(task.schedule.kind)}</span>
      <div className="relative hidden shrink-0 group-hover:block" ref={menuRef}>
        <button
          type="button"
          aria-label={`Options for ${task.name}`}
          className="rounded-md p-1 text-[var(--muted)] hover:bg-[var(--border)]"
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen((v) => !v);
          }}
        >
          <MoreIcon className="h-3.5 w-3.5" />
        </button>
        {menuOpen && (
          <div
            className="absolute right-0 top-full z-10 mt-1 min-w-32 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] py-1 shadow-[var(--shadow)]"
            onClick={(e) => e.stopPropagation()}
          >
            <button type="button" className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]" onClick={runNow}>
              <PlayIcon className="h-3.5 w-3.5" /> Run now
            </button>
            <button
              type="button"
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]"
              onClick={(e) => {
                e.stopPropagation();
                setMenuOpen(false);
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
                setMenuOpen(false);
                setDeleteConfirm(true);
              }}
            >
              <TrashIcon className="h-3.5 w-3.5" /> Delete
            </button>
          </div>
        )}
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

interface NavRailProps {
  threadId: string;
  mode: NavMode;
  onModeChange: (mode: NavMode) => void;
  // Only the setter is needed now -- the sidebar always shows the
  // Scheduled task list unconditionally (see the removed +New/Scheduled
  // toggle pair's own docstring below), it just still needs to *set*
  // runTab to "scheduled" when the outer clock icon is clicked.
  onRunTabChange: (tab: RunTab) => void;
  workflowRuns: WorkflowRun[];
  onStop: () => void;
  scheduledTasksVersion: number;
  // Plain state clear (the "Scheduled" button's own reset-to-portal
  // click) vs. the "smart open" a row click gets (goToThread if the task
  // has already run, otherwise the same select) -- see App.tsx's
  // openScheduledTask for why these can't be the same function: a row
  // click always wants "open," but nothing here ever wants to force-
  // navigate on a plain clear.
  onSelectScheduledTask: (task: ScheduledTask | null) => void;
  onOpenScheduledTask: (task: ScheduledTask) => void;
  onEditScheduledTask: (task: ScheduledTask | null) => void;
}

/** Replaces the old top-bar "Sessions" dropdown -- a narrow icon-only
 * rail that expands into a full nav panel on hover (matching the "Nav
 * rail" reference screenshot, docs/ui-references/nav-rail.png), rather
 * than a click-to-open dropdown or the old Create/Run pill-button-inside-
 * the-panel shape. Collapsed, it shows the pin toggle plus two always-
 * visible mode icons (chat/workflow -- switches `mode` directly, no need
 * to expand first); expanded, it adds a flat nav-item list below them:
 * just "New" in chat mode (ported from SessionMenu.tsx's session list),
 * or "New" + "Scheduled" in workflow mode (ported from
 * SessionMenu.tsx's Workflows/Recent Runs sections plus
 * settings/ScheduledTasksTab.tsx -- "history" folded into "Scheduled",
 * see RunTab's own comment). Each flat item is its own little accordion
 * within the rail: click to expand/collapse its list in place, which
 * also drives RunPanel's own main-content view via onRunTabChange (today
 * that's the *only* way to switch RunPanel's section -- RunPanel itself
 * renders no tab UI of its own). Settings itself is reachable from the
 * header row (App.tsx), not from here -- it used to live at the bottom of
 * this rail, but the user pointed out that put it out of the same visual
 * row as the sidebar toggle it's paired with. */
export function NavRail({
  threadId,
  mode,
  onModeChange,
  onRunTabChange,
  workflowRuns,
  onStop,
  scheduledTasksVersion,
  onSelectScheduledTask,
  onOpenScheduledTask,
  onEditScheduledTask,
}: NavRailProps) {
  // Not persisted (no localStorage) -- explicit call: pin is a per-page-
  // load convenience, not a remembered setting.
  const [pinned, setPinned] = useState(false);
  const [hovering, setHovering] = useState(false);
  const expanded = pinned || hovering;
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [scheduledTasks, setScheduledTasks] = useState<ScheduledTask[]>([]);
  const [deleteTarget, setDeleteTarget] = useState<ThreadSummary | null>(null);
  const [runDeleteTarget, setRunDeleteTarget] = useState<string | null>(null);

  useEffect(() => {
    if (!expanded) return;
    getThreads().then(setThreads);
    getScheduledTasks().then(setScheduledTasks);
  }, [expanded, scheduledTasksVersion]);

  const currentRun = workflowRuns.find((run) => run.status === "running");
  const recentRuns = [...workflowRuns].sort((a, b) => b.started_at.localeCompare(a.started_at)).slice(0, 8);

  const renameThreadLocally = (id: string, title: string) => {
    setThreads((prev) => prev.map((t) => (t.thread_id === id ? { ...t, preview: title } : t)));
  };

  const confirmDelete = () => {
    if (!deleteTarget) return;
    const id = deleteTarget.thread_id;
    setDeleteTarget(null);
    deleteThread(id).then(() => getThreads().then(setThreads));
  };

  const confirmRemoveRun = () => {
    if (!runDeleteTarget) return;
    const runId = runDeleteTarget;
    setRunDeleteTarget(null);
    deleteWorkflowRun(runId);
  };

  return (
    <>
      <div
        className={`absolute left-0 top-0 z-30 flex flex-col transition-[width] duration-150 ease-out ${
          expanded ? "h-full border-r border-[var(--border)] bg-[var(--panel-bg)]" : "h-12"
        }`}
        style={{ width: expanded ? 272 : 48 }}
        onMouseEnter={() => setHovering(true)}
        onMouseLeave={() => setHovering(false)}
      >
        {/* Collapsed (48px), this row is just the pin toggle, centered like
         * the old lone toggle button. Expanded, the mode-icon pair joins it
         * on the same row -- they only need to be reachable once the panel
         * is already open (hover or pinned), not squeezed into the 48px
         * sliver too. */}
        <div className={`flex h-12 shrink-0 items-center px-1.5 ${expanded ? "justify-between" : "justify-center"}`}>
          <button
            type="button"
            title={pinned ? "Unpin navigation" : "Pin navigation open"}
            className={`flex h-9 w-9 items-center justify-center rounded-md hover:bg-[var(--card-bg)] ${
              pinned ? "text-[var(--fg)]" : "text-[var(--muted)] hover:text-[var(--fg)]"
            }`}
            onClick={() => setPinned((v) => !v)}
          >
            <SidebarIcon className="h-[18px] w-[18px]" />
          </button>
          {expanded && (
            <div className="flex gap-0.5">
              <button
                type="button"
                title="Chat"
                className={`flex h-8 w-8 items-center justify-center rounded-md ${
                  mode === "create" ? "bg-[var(--card-bg)] text-[var(--fg)]" : "text-[var(--muted)] hover:text-[var(--fg)]"
                }`}
                onClick={() => onModeChange("create")}
              >
                <MessageCircleIcon className="h-[16px] w-[16px]" />
              </button>
              <button
                type="button"
                title="Scheduled"
                className={`flex h-8 w-8 items-center justify-center rounded-md ${
                  mode === "run" ? "bg-[var(--card-bg)] text-[var(--fg)]" : "text-[var(--muted)] hover:text-[var(--fg)]"
                }`}
                onClick={() => {
                  onModeChange("run");
                  onRunTabChange("scheduled");
                  // Always lands on the portal grid, never a stale
                  // detail-page selection left over from before the user
                  // switched away to Chat mode and back.
                  onSelectScheduledTask(null);
                }}
              >
                <ClockIcon className="h-[16px] w-[16px]" />
              </button>
            </div>
          )}
        </div>

        {expanded && (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-2 pb-2">
            {mode === "create" && (
              <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
                {currentRun && (
                  <div className="mb-2 rounded-md border border-[var(--border)] p-2">
                    <div className="flex min-w-0 items-center justify-between text-sm">
                      <span className="min-w-0 truncate font-medium">{currentRun.workflow_name} running...</span>
                      <button type="button" className="rounded-md border border-[var(--border)] px-2 py-0.5 text-xs" onClick={onStop}>
                        Stop
                      </button>
                    </div>
                  </div>
                )}
                <button
                  type="button"
                  className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm font-medium hover:bg-[var(--card-bg)]"
                  onClick={startNewSession}
                >
                  {/* Solid black "add" treatment (see index.css's palette
                   * comment) -- a filled --primary badge stays visible on
                   * any surface and reads as the one clearly primary
                   * action here, apart from the two lighter accent hues. */}
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--primary)] text-[var(--primary-fg)]">
                    <PlusIcon className="h-3 w-3" />
                  </span>
                  New session
                </button>
                <div className="mb-1 mt-2 px-2 text-xs font-medium tracking-wide text-[var(--muted)]">RECENTS</div>
                {threads.length === 0 && <div className="px-2 py-1 text-sm text-[var(--muted)]">No sessions yet.</div>}
                {threads.map((thread) => (
                  <ThreadRow
                    key={thread.thread_id}
                    thread={thread}
                    isCurrent={thread.thread_id === threadId}
                    onRenamed={(title) => renameThreadLocally(thread.thread_id, title)}
                    onDeleteRequest={() => setDeleteTarget(thread)}
                  />
                ))}
              </div>
            )}
  
            {mode === "run" && (
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                {currentRun && (
                  <div className="mb-2 rounded-md border border-[var(--border)] p-2">
                    <div className="flex min-w-0 items-center justify-between text-sm">
                      <span className="min-w-0 truncate font-medium">{currentRun.workflow_name} running...</span>
                      <button type="button" className="rounded-md border border-[var(--border)] px-2 py-0.5 text-xs" onClick={onStop}>
                        Stop
                      </button>
                    </div>
                  </div>
                )}
                {/* The old flat "+New" (saved Workflow-defs)/"Scheduled"
                 * toggle pair is gone -- the outer mode-icon button (the
                 * clock icon above) already jumps straight here, so a
                 * second "Scheduled" toggle inside the panel was pure
                 * redundancy, and the saved-Workflow-defs list it toggled
                 * was an unrelated, unused feature per explicit request.
                 * "+ New task" replaces both -- same visual treatment as
                 * Chat mode's own "New session" button above, but a stub
                 * for now (equivalent to the portal's own disabled
                 * "Create with coscribe" item, not yet built: see
                 * RunPanel.tsx's identical stub for why). The task list
                 * below is unconditional now -- nothing left to toggle. */}
                <button
                  type="button"
                  disabled
                  title="Not available yet -- see the portal's New task menu"
                  className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm font-medium opacity-60"
                >
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--primary)] text-[var(--primary-fg)]">
                    <PlusIcon className="h-3 w-3" />
                  </span>
                  New task
                </button>
                <div className="mt-1 flex-1 overflow-y-auto">
                  {scheduledTasks.length === 0 && <div className="px-2 py-1 text-sm text-[var(--muted)]">No scheduled tasks yet.</div>}
                  {scheduledTasks.map((task) => (
                    <ScheduledTaskRow
                      key={task.trigger_id}
                      task={task}
                      onSelect={() => onOpenScheduledTask(task)}
                      onEdit={() => onEditScheduledTask(task)}
                      onChanged={() => getScheduledTasks().then(setScheduledTasks)}
                    />
                  ))}
                  {/* "History" (recent runs) has no flat-nav slot of its
                   * own -- folded in here under Scheduled, per explicit
                   * call: both are "ran without you typing a message
                   * right now." */}
                  {recentRuns.length > 0 && (
                    <div className="mb-1 mt-2 px-2 text-xs font-medium tracking-wide text-[var(--muted)]">RECENT RUNS</div>
                  )}
                  {recentRuns.map((run) => (
                    <div key={run.run_id} className="group flex items-center justify-between gap-1 rounded-md px-2 py-1.5 text-sm">
                      <div className="flex min-w-0 items-center gap-1.5">
                        <ClockIcon className="h-3.5 w-3.5 shrink-0 text-[var(--muted)]" />
                        <span className="truncate">{run.workflow_name}</span>
                        <span className="shrink-0 text-xs text-[var(--muted)]">({run.status})</span>
                      </div>
                      {run.status !== "running" && (
                        <button
                          type="button"
                          aria-label="Delete run record"
                          className="shrink-0 rounded-md px-1 text-[var(--muted)] opacity-0 hover:bg-[var(--border)] group-hover:opacity-100"
                          onClick={() => setRunDeleteTarget(run.run_id)}
                        >
                          &times;
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
      {deleteTarget && (
        <ConfirmDialog
          title="Delete chat?"
          description={
            <span className="block truncate" title={deleteTarget.preview || deleteTarget.thread_id}>
              "{deleteTarget.preview || deleteTarget.thread_id}" and its history will be permanently removed.
            </span>
          }
          onCancel={() => setDeleteTarget(null)}
          onConfirm={confirmDelete}
        />
      )}
      {runDeleteTarget && (
        <ConfirmDialog
          title="Delete run record?"
          description="This only removes the history entry -- it does not affect any files the run created."
          onCancel={() => setRunDeleteTarget(null)}
          onConfirm={confirmRemoveRun}
        />
      )}
    </>
  );
}
