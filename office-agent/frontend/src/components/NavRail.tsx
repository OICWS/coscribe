import { useEffect, useRef, useState } from "react";
import { deleteThread, deleteWorkflowRun, getScheduledTasks, getThreads, getWorkflows, renameThread } from "../lib/rest";
import { useClickOutside } from "../lib/useClickOutside";
import type { ScheduledTask } from "../types/settings";
import type { ThreadSummary } from "../types/session";
import type { Workflow } from "../types/settings";
import type { WorkflowRun } from "../types/wire";
import { ConfirmDialog } from "./ConfirmDialog";
import { CalendarIcon, ClockIcon, MoreIcon, PencilIcon, PlusIcon, SidebarIcon, TrashIcon, ZapIcon } from "./icons";

export type NavMode = "create" | "run";
export type RunTab = "workflows" | "scheduled" | "history";

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
 * rail, Create/Run split" design pass), rather than a click-to-open
 * dropdown. Collapsed, it shows nothing but the toggle icon; expanded, it
 * shows the Create/Run mode switch and, depending on which mode is
 * active, either the session list (Create, ported from SessionMenu.tsx)
 * or the Workflows/Scheduled/History sub-tabs (Run, ported from
 * SessionMenu.tsx's own Workflows/Recent Runs sections plus
 * settings/ScheduledTasksTab.tsx). Settings itself is reachable from the
 * header row (App.tsx), not from here -- it used to live at the bottom of
 * this rail, but the user pointed out that put it out of the same visual
 * row as the sidebar toggle it's paired with. RunPanel (the main content
 * area for Run mode) fetches this same data independently for its own
 * full detail view, same "each surface fetches its own slice on
 * mount/open" pattern already used by the Settings tabs -- this rail's
 * copy is a compact quick-list, not the source of truth. */
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
  const [expanded, setExpanded] = useState(false);
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
        onMouseEnter={() => setExpanded(true)}
        onMouseLeave={() => setExpanded(false)}
      >
        <button
          type="button"
          title="Toggle navigation"
          className="flex h-12 w-12 shrink-0 items-center justify-center text-[var(--muted)] hover:text-[var(--fg)]"
        >
          <SidebarIcon className="h-[18px] w-[18px]" />
        </button>
  
        {expanded && (
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-2 pb-2">
            <div className="mb-2 flex gap-1 rounded-lg bg-[var(--card-bg)] p-1">
              <button
                type="button"
                className={`flex-1 rounded-md py-1 text-sm font-medium ${
                  mode === "create" ? "bg-[var(--accent)] text-[var(--accent-fg)]" : "text-[var(--muted)] hover:text-[var(--fg)]"
                }`}
                onClick={() => onModeChange("create")}
              >
                Create
              </button>
              <button
                type="button"
                className={`flex-1 rounded-md py-1 text-sm font-medium ${
                  mode === "run" ? "bg-[var(--accent)] text-[var(--accent-fg)]" : "text-[var(--muted)] hover:text-[var(--fg)]"
                }`}
                onClick={() => onModeChange("run")}
              >
                Run
              </button>
            </div>
  
            {mode === "create" && (
              <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
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
                  className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-sm font-medium text-[var(--accent)] hover:bg-[var(--card-bg)]"
                  onClick={startNewSession}
                >
                  <PlusIcon className="h-3.5 w-3.5" /> New session
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
                <div className="mb-1 flex gap-3 border-b border-[var(--border)] px-1 text-sm">
                  {(["workflows", "scheduled", "history"] as const).map((tab) => (
                    <button
                      key={tab}
                      type="button"
                      className={`-mb-px border-b-2 py-1.5 capitalize ${
                        runTab === tab ? "border-[var(--accent)] font-medium text-[var(--accent)]" : "border-transparent text-[var(--muted)] hover:text-[var(--fg)]"
                      }`}
                      onClick={() => onRunTabChange(tab)}
                    >
                      {tab}
                    </button>
                  ))}
                </div>
                <div className="flex-1 overflow-y-auto">
                  {runTab === "workflows" && (
                    <>
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
                    </>
                  )}
                  {runTab === "scheduled" && (
                    <>
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
                    </>
                  )}
                  {runTab === "history" && (
                    <>
                      {recentRuns.length === 0 && <div className="px-2 py-1 text-sm text-[var(--muted)]">No runs yet.</div>}
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
                    </>
                  )}
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
