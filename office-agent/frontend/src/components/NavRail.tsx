import { useEffect, useRef, useState } from "react";
import { deleteScheduledTask, deleteThread, renameThread } from "../lib/rest";
import { useClickOutside } from "../lib/useClickOutside";
import { goToThread, startNewThread } from "../lib/nav";
import { latestRun } from "../lib/runLabels";
import { scheduleKindLabel } from "../lib/scheduleLabels";
import type { ScheduledTask } from "../types/settings";
import type { ThreadSummary } from "../types/session";
import { ConfirmDialog } from "./ConfirmDialog";
import { RunStatusIcon } from "./RunStatusIcon";
import { COLLAPSED_CLUSTER_WIDTH, DRAWS_TITLE_BAR } from "../lib/titleBar";
import { AppMenuButton, HistoryButtons } from "./WindowControls";
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
  active: boolean;
  onSelect: () => void;
  onEdit: () => void;
  onRunNow: () => void;
  onChanged: () => void;
}

/** One Scheduled sidebar row -- matches
 * docs/ui-references/sheduled-sidebar-workflow-display.png: a leading
 * bullet, the name, and (until hovered) the schedule kind right-aligned
 * in muted text; hovering swaps that label for a "..." menu (Run now/
 * Edit/Delete -- no Pause, see sheduled-siderbar-workflow-display-
 * settings.png, unlike the portal card's own menu which keeps it).
 * Clicking the row opens its latest run (or the task's page, before it
 * has run). A running or stalled latest run replaces the bullet with its
 * status, so it's visible without opening anything. */
function ScheduledTaskRow({ task, active, onSelect, onEdit, onRunNow, onChanged }: ScheduledTaskRowProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useClickOutside(menuRef, () => setMenuOpen(false), menuOpen);

  const runNow = () => {
    setMenuOpen(false);
    onRunNow();
  };
  const run = latestRun(task);
  const showStatus = run !== null && (run.status === "running" || run.status === "needs_approval" || run.status === "failed");

  const confirmDelete = async () => {
    setDeleteConfirm(false);
    await deleteScheduledTask(task.trigger_id);
    onChanged();
  };

  return (
    <div
      className={`group flex min-w-0 cursor-pointer items-center gap-1.5 rounded-md px-2 py-1.5 text-sm hover:bg-[var(--card-bg)] ${
        active ? "bg-[var(--card-bg)] font-medium" : ""
      }`}
      onClick={onSelect}
    >
      {showStatus ? (
        <span className="flex w-3 shrink-0 justify-center">
          <RunStatusIcon status={run.status} className="h-3 w-3" />
        </span>
      ) : (
        <span className="w-3 shrink-0 text-center text-[var(--muted)]">○</span>
      )}
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
          description={`"${task.name}" and the conversations of its runs will be permanently removed.`}
          onCancel={() => setDeleteConfirm(false)}
          onConfirm={confirmDelete}
        />
      )}
    </div>
  );
}

export const NAV_RAIL_EXPANDED_WIDTH = 272;

/** How far right of the open panel the pointer may wander before a
 * hover-opened panel closes. */
const HOVER_CLOSE_MARGIN = 48;

// In the desktop shell the top row is the title bar's left end.
const TOP_ROW_HEIGHT = DRAWS_TITLE_BAR ? "h-10" : "h-12";

interface NavRailProps {
  threadId: string;
  pinned: boolean;
  onPinnedChange: (pinned: boolean) => void;
  threads: ThreadSummary[];
  onThreadsChanged: () => void;
  onThreadRenamed: (threadId: string, title: string) => void;
  mode: NavMode;
  onModeChange: (mode: NavMode) => void;
  scheduledTasks: ScheduledTask[];
  onScheduledTasksChanged: () => void;
  /** The task whose page or run is currently open, highlighted. */
  activeTaskId: string | null;
  onOpenScheduledTask: (task: ScheduledTask) => void;
  onEditScheduledTask: (task: ScheduledTask | null) => void;
  onRunScheduledTaskNow: (task: ScheduledTask) => void;
  onNewScheduledTask: () => void;
}

/** A narrow icon-only rail that expands into a full nav panel on hover
 * or when pinned (docs/ui-references/nav-rail.png). Collapsed, it shows
 * the pin toggle and the two mode icons (chat / scheduled); expanded, it
 * lists either chat sessions or scheduled tasks, depending on mode. */
export function NavRail({
  threadId,
  pinned,
  onPinnedChange,
  threads,
  onThreadsChanged,
  onThreadRenamed,
  mode,
  onModeChange,
  scheduledTasks,
  onScheduledTasksChanged,
  activeTaskId,
  onOpenScheduledTask,
  onEditScheduledTask,
  onRunScheduledTaskNow,
  onNewScheduledTask,
}: NavRailProps) {
  const [hovering, setHovering] = useState(false);
  const expanded = pinned || hovering;

  // A hover-opened panel closes once the pointer is well clear of it, not
  // the moment it leaves: the desktop title bar's drag area swallows mouse
  // events, so a plain mouseleave fired as soon as the pointer left a
  // button there.
  useEffect(() => {
    if (!hovering || pinned) return;
    const onMove = (event: MouseEvent) => {
      if (event.clientX > NAV_RAIL_EXPANDED_WIDTH + HOVER_CLOSE_MARGIN) setHovering(false);
    };
    const onBlur = () => setHovering(false);
    document.addEventListener("mousemove", onMove);
    window.addEventListener("blur", onBlur);
    return () => {
      document.removeEventListener("mousemove", onMove);
      window.removeEventListener("blur", onBlur);
    };
  }, [hovering, pinned]);
  const [deleteTarget, setDeleteTarget] = useState<ThreadSummary | null>(null);

  useEffect(() => {
    if (expanded) onThreadsChanged();
  }, [expanded, onThreadsChanged]);


  const confirmDelete = () => {
    if (!deleteTarget) return;
    const id = deleteTarget.thread_id;
    setDeleteTarget(null);
    deleteThread(id).then(onThreadsChanged);
  };

  return (
    <>
      <div
        className={`absolute left-0 top-0 z-30 flex flex-col transition-[width] duration-150 ease-out ${
          expanded ? "h-full border-r border-[var(--border)] bg-[var(--panel-bg)]" : TOP_ROW_HEIGHT
        }`}
        style={{ width: expanded ? NAV_RAIL_EXPANDED_WIDTH : COLLAPSED_CLUSTER_WIDTH }}
        onMouseEnter={DRAWS_TITLE_BAR ? undefined : () => setHovering(true)}
      >
        {/* Collapsed, this row is the pin toggle (plus, in the desktop
         * shell, the app menu and Back/Forward). Expanded, the mode-icon
         * pair joins it -- they only need to be reachable once the panel
         * is already open (hover or pinned). */}
        <div
          className={`titlebar-drag flex ${TOP_ROW_HEIGHT} shrink-0 items-center gap-0.5 px-1.5 ${
            expanded ? "justify-between" : DRAWS_TITLE_BAR ? "justify-start" : "justify-center"
          }`}
        >
          <div className="flex items-center gap-0.5">
            {DRAWS_TITLE_BAR && <AppMenuButton />}
            <button
              type="button"
              title={pinned ? "Unpin navigation" : "Pin navigation open"}
              className={`flex h-9 w-9 items-center justify-center rounded-md hover:bg-[var(--card-bg)] ${
                pinned ? "text-[var(--fg)]" : "text-[var(--muted)] hover:text-[var(--fg)]"
              }`}
              onClick={() => onPinnedChange(!pinned)}
              // The desktop row also holds the menu and Back/Forward, which
              // mustn't pop the panel open; only this button does.
              onMouseEnter={DRAWS_TITLE_BAR ? () => setHovering(true) : undefined}
            >
              <SidebarIcon className="h-[18px] w-[18px]" />
            </button>
            {DRAWS_TITLE_BAR && <HistoryButtons />}
          </div>
          {expanded && (
            <div className="flex rounded-lg bg-[var(--card-bg)] p-0.5">
              {(
                [
                  { value: "create", title: "Chat", Icon: MessageCircleIcon },
                  { value: "run", title: "Scheduled", Icon: ClockIcon },
                ] as const
              ).map(({ value, title, Icon }) => (
                <button
                  key={value}
                  type="button"
                  title={title}
                  aria-pressed={mode === value}
                  className={`flex h-7 w-8 items-center justify-center rounded-md ${
                    mode === value
                      ? "bg-[var(--bg)] text-[var(--fg)] shadow-sm ring-1 ring-[var(--border)]"
                      : "text-[var(--muted)] hover:text-[var(--fg)]"
                  }`}
                  onClick={() => onModeChange(value)}
                >
                  <Icon className="h-[15px] w-[15px]" />
                </button>
              ))}
            </div>
          )}
        </div>

        {expanded && (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-2 pb-2">
            {mode === "create" && (
              <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
                <button
                  type="button"
                  className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm font-medium hover:bg-[var(--card-bg)]"
                  onClick={startNewThread}
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
                    onRenamed={(title) => onThreadRenamed(thread.thread_id, title)}
                    onDeleteRequest={() => setDeleteTarget(thread)}
                  />
                ))}
              </div>
            )}
  
            {mode === "run" && (
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                <button
                  type="button"
                  className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm font-medium hover:bg-[var(--card-bg)]"
                  onClick={onNewScheduledTask}
                >
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--primary)] text-[var(--primary-fg)]">
                    <PlusIcon className="h-3 w-3" />
                  </span>
                  New task
                </button>
                <div className="mb-1 mt-2 px-2 text-xs font-medium tracking-wide text-[var(--muted)]">TASKS</div>
                <div className="flex-1 overflow-y-auto">
                  {scheduledTasks.length === 0 && <div className="px-2 py-1 text-sm text-[var(--muted)]">No scheduled tasks yet.</div>}
                  {scheduledTasks.map((task) => (
                    <ScheduledTaskRow
                      key={task.trigger_id}
                      task={task}
                      active={task.trigger_id === activeTaskId}
                      onSelect={() => onOpenScheduledTask(task)}
                      onEdit={() => onEditScheduledTask(task)}
                      onRunNow={() => onRunScheduledTaskNow(task)}
                      onChanged={onScheduledTasksChanged}
                    />
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
    </>
  );
}
