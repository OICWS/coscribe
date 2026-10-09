import { useEffect, useRef, useState } from "react";
import { deleteScheduledTask, deleteThread } from "../lib/rest";
import { useClickOutside } from "../lib/useClickOutside";
import { startNewThread } from "../lib/nav";
import { latestRun, RUN_STATUS_LABEL } from "../lib/runLabels";
import { scheduleKindLabel } from "../lib/scheduleLabels";
import type { RunStatus, ScheduledTask } from "../types/settings";
import type { ThreadSummary } from "../types/session";
import { ConfirmDialog } from "./ConfirmDialog";
import { RunStatusIcon } from "./RunStatusIcon";
import { ThreadList } from "./ThreadList";
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

interface ScheduledTaskRowProps {
  task: ScheduledTask;
  active: boolean;
  onSelect: () => void;
  onEdit: () => void;
  onRunNow: () => void;
  onChanged: () => void;
}

const DOT_COLOR: Partial<Record<RunStatus, string>> = {
  completed: "var(--success)",
  failed: "var(--danger)",
  needs_approval: "var(--warning)",
};

/** The task's latest run at a glance: a spinner while it runs, then a
 * solid dot in the outcome's color; hollow before any run, or once
 * stopped. */
function RunDot({ status }: { status: RunStatus | null }) {
  if (status === "running") return <RunStatusIcon status="running" className="h-2.5 w-2.5" />;
  const color = status ? DOT_COLOR[status] : undefined;
  return (
    <span
      role="img"
      aria-label={status ? RUN_STATUS_LABEL[status] : "Not run yet"}
      className="inline-block h-2 w-2 shrink-0 rounded-full border-[1.5px]"
      style={color ? { background: color, borderColor: color } : { borderColor: "var(--muted)" }}
    />
  );
}

/** One Scheduled sidebar row: a leading
 * bullet, the name, and (until hovered) the schedule kind right-aligned
 * in muted text; hovering swaps that label for a "..." menu (Run now/
 * Edit/Delete -- no Pause, unlike the portal card's own menu which keeps
 * it).
 * Clicking the row opens its latest run (or the task's page, before it
 * has run). Its dot shows how the latest run is going or went, so that's
 * visible without opening anything. */
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
      <span className="flex w-3 shrink-0 justify-center">
        <RunDot status={run?.status ?? null} />
      </span>
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

/** How long a hover-opened panel stays once the pointer is out of it. */
const HOVER_CLOSE_DELAY_MS = 600;

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
 * or when pinned. Collapsed, it shows
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

  // A hover-opened panel closes a moment after the pointer is out of it,
  // not the instant it leaves: the desktop title bar's drag area swallows
  // mouse events, so a plain mouseleave fired as soon as the pointer left a
  // button there. The pointer's position is read instead, and coming back
  // within the delay keeps the panel.
  useEffect(() => {
    if (!hovering || pinned) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onMove = (event: MouseEvent) => {
      if (event.clientX <= NAV_RAIL_EXPANDED_WIDTH) {
        clearTimeout(timer);
        timer = undefined;
      } else if (timer === undefined) {
        timer = setTimeout(() => {
          timer = undefined;
          // A dialog opened from the panel (Edit environment) is mounted in it:
          // closing the panel would take the dialog away mid-use. The next
          // move, once it is closed, starts the delay again.
          if (!document.querySelector(".fixed.inset-0")) setHovering(false);
        }, HOVER_CLOSE_DELAY_MS);
      }
    };
    const onBlur = () => setHovering(false);
    document.addEventListener("mousemove", onMove);
    window.addEventListener("blur", onBlur);
    return () => {
      clearTimeout(timer);
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
              <div className="flex min-h-0 flex-1 flex-col gap-0.5">
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
                <ThreadList
                  threads={threads}
                  currentId={threadId}
                  onChanged={onThreadsChanged}
                  onRenamed={onThreadRenamed}
                  onDeleteRequest={setDeleteTarget}
                />
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
