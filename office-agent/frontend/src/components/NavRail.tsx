import { useEffect, useRef, useState } from "react";
import { deleteThread, deleteWorkflowRun, getScheduledTasks, getThreads, getWorkflows, renameThread } from "../lib/rest";
import { useClickOutside } from "../lib/useClickOutside";
import type { ScheduledTask } from "../types/settings";
import type { ThreadSummary } from "../types/session";
import type { Workflow } from "../types/settings";
import type { WorkflowRun } from "../types/wire";
import { ConfirmDialog } from "./ConfirmDialog";
import {
  CalendarIcon,
  ClockIcon,
  MessageCircleIcon,
  MoreIcon,
  PencilIcon,
  PlusIcon,
  SidebarIcon,
  TrashIcon,
  ZapIcon,
} from "./icons";

export type NavMode = "create" | "run";
// "history" folded into "scheduled" -- both are "things that ran without
// you typing a message right now," and the flat New/Scheduled nav shape
// (see NavRail's own docstring below) has no third slot for it.
export type RunTab = "workflows" | "scheduled";

/** Full-page reload, same as SessionMenu.tsx's identical helper -- there is
 * no in-page thread-switching machinery, deliberately not ported. */
function goToThread(id: string) {
  window.location.href = `${window.location.pathname}?thread=${id}`;
}

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
      className={`group flex min-w-0 items-center justify-between gap-1 rounded-md bg-[var(--card-bg)] px-2 py-1.5 text-sm hover:bg-[var(--card-bg-hover)] ${
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

interface NavRailProps {
  threadId: string;
  mode: NavMode;
  onModeChange: (mode: NavMode) => void;
  runTab: RunTab;
  onRunTabChange: (tab: RunTab) => void;
  workflowRuns: WorkflowRun[];
  onRunWorkflow: (name: string) => void;
  onStop: () => void;
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
  runTab,
  onRunTabChange,
  workflowRuns,
  onRunWorkflow,
  onStop,
}: NavRailProps) {
  // Not persisted (no localStorage) -- explicit call: pin is a per-page-
  // load convenience, not a remembered setting.
  const [pinned, setPinned] = useState(false);
  const [hovering, setHovering] = useState(false);
  const expanded = pinned || hovering;
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [scheduledTasks, setScheduledTasks] = useState<ScheduledTask[]>([]);
  const [deleteTarget, setDeleteTarget] = useState<ThreadSummary | null>(null);
  const [runDeleteTarget, setRunDeleteTarget] = useState<string | null>(null);

  useEffect(() => {
    if (!expanded) return;
    getThreads().then(setThreads);
    getWorkflows().then(setWorkflows);
    getScheduledTasks().then(setScheduledTasks);
  }, [expanded]);

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
    deleteWorkflowRun(runId).then(() => getWorkflows());
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
                title="Workflow"
                className={`flex h-8 w-8 items-center justify-center rounded-md ${
                  mode === "run" ? "bg-[var(--card-bg)] text-[var(--fg)]" : "text-[var(--muted)] hover:text-[var(--fg)]"
                }`}
                onClick={() => onModeChange("run")}
              >
                <ZapIcon className="h-[16px] w-[16px]" />
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
                  className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm font-medium hover:bg-[var(--bg)]"
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
                {/* Flat New/Scheduled nav items, each its own accordion --
                 * clicking one both expands its list here and (via
                 * onRunTabChange) switches which section RunPanel's main
                 * view shows, since RunPanel has no tab UI of its own. */}
                <button
                  type="button"
                  className={`flex items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-sm font-medium hover:bg-[var(--card-bg)] ${
                    runTab === "workflows" ? "text-[var(--accent)]" : ""
                  }`}
                  onClick={() => onRunTabChange("workflows")}
                >
                  <PlusIcon className="h-3.5 w-3.5" /> New
                </button>
                {runTab === "workflows" && (
                  <div className="mb-1 flex-1 overflow-y-auto">
                    {workflows.length === 0 && <div className="px-2 py-1 text-sm text-[var(--muted)]">No workflows saved yet.</div>}
                    {workflows.map((wf) => (
                      <button
                        key={wf.name}
                        type="button"
                        className="flex w-full min-w-0 items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]"
                        onClick={() => onRunWorkflow(wf.name)}
                      >
                        <ZapIcon className="h-3.5 w-3.5 shrink-0 text-[var(--muted)]" />
                        <span className="min-w-0 truncate">{wf.name}</span>
                      </button>
                    ))}
                  </div>
                )}
                <button
                  type="button"
                  className={`flex items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-sm font-medium hover:bg-[var(--card-bg)] ${
                    runTab === "scheduled" ? "text-[var(--accent)]" : "text-[var(--muted)] hover:text-[var(--fg)]"
                  }`}
                  onClick={() => onRunTabChange("scheduled")}
                >
                  <CalendarIcon className="h-3.5 w-3.5" /> Scheduled
                </button>
                {runTab === "scheduled" && (
                  <div className="flex-1 overflow-y-auto">
                    {scheduledTasks.length === 0 && <div className="px-2 py-1 text-sm text-[var(--muted)]">No scheduled tasks yet.</div>}
                    {scheduledTasks.map((task) => (
                      <div key={task.trigger_id} className="flex items-start gap-1.5 rounded-md px-2 py-1.5 text-sm">
                        <CalendarIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--muted)]" />
                        <div className="min-w-0">
                          <div className="truncate">{task.name}</div>
                          <div className="truncate text-xs text-[var(--muted)]">{task.enabled ? "enabled" : "paused"}</div>
                        </div>
                      </div>
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
                )}
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
