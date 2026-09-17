import { useEffect, useState } from "react";
import {
  getSubAgentTasks,
  getSubAgentTranscript,
  pauseSubAgentTask,
  resumeSubAgentTask,
} from "../lib/rest";
import type { SubAgentTask } from "../types/session";
import type { HistoryEntry } from "../types/wire";
import {
  ArrowLeftIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  ClockIcon,
  CloseIcon,
  PauseIcon,
  PlayIcon,
  ToolIcon,
  XCircleIcon,
} from "./icons";

// Plain polling, not a push channel -- see web/session.py's
// notify_resync docstring for why: this app only ever has one browser
// tab watching a thread at a time in practice, and the "post-hoc, see
// the full record once it's done" bar the user set for this panel
// (rather than live token streaming) doesn't need anything tighter than
// a few-second refresh while the panel happens to be open.
const POLL_INTERVAL_MS = 3000;

const STATUS_LABEL: Record<SubAgentTask["status"], string> = {
  running: "Running",
  paused: "Paused",
  blocked_on_approval: "Needs approval",
  succeeded: "Done",
  failed: "Failed",
};

function StatusBadge({ status }: { status: SubAgentTask["status"] }) {
  const colorClass =
    status === "succeeded"
      ? "text-[var(--accent)]"
      : status === "failed"
        ? "text-[var(--danger)]"
        : status === "running"
          ? "text-[var(--fg)]"
          : "text-yellow-600 dark:text-yellow-400";
  return (
    <span className={`flex shrink-0 items-center gap-1 text-xs font-medium ${colorClass}`}>
      {status === "running" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
      {status === "succeeded" && <CheckCircleIcon className="h-3.5 w-3.5" />}
      {status === "failed" && <XCircleIcon className="h-3.5 w-3.5" />}
      {(status === "paused" || status === "blocked_on_approval") && <ClockIcon className="h-3.5 w-3.5" />}
      {STATUS_LABEL[status]}
    </span>
  );
}

function relativeTime(iso: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleDateString();
}

function ToolEntryView({ entry }: { entry: Extract<HistoryEntry, { kind: "tool" }> }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--card-bg)] text-sm">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <ToolIcon
          className={`h-3.5 w-3.5 shrink-0 ${entry.is_error ? "text-[var(--danger)]" : "text-[var(--muted)]"}`}
        />
        <span className="min-w-0 flex-1 truncate font-mono text-xs">{entry.tool_name}</span>
        <ChevronDownIcon
          className={`h-3.5 w-3.5 shrink-0 text-[var(--muted)] transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="flex flex-col gap-2 border-t border-[var(--border)] px-3 py-2 font-mono text-xs">
          <div>
            <div className="mb-0.5 text-[var(--muted)]">Arguments</div>
            <pre className="overflow-x-auto whitespace-pre-wrap break-words">
              {JSON.stringify(entry.arguments, null, 2)}
            </pre>
          </div>
          <div>
            <div className="mb-0.5 text-[var(--muted)]">Result</div>
            <pre className={`overflow-x-auto whitespace-pre-wrap break-words ${entry.is_error ? "text-[var(--danger)]" : ""}`}>
              {typeof entry.result === "string" ? entry.result : JSON.stringify(entry.result, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

function TranscriptEntryView({ entry }: { entry: HistoryEntry }) {
  if (entry.kind === "tool") return <ToolEntryView entry={entry} />;
  if (entry.kind === "user") {
    return (
      <div className="rounded-lg bg-[var(--card-bg)] px-3 py-2 text-sm">
        <div className="mb-1 text-xs font-medium uppercase tracking-wide text-[var(--muted)]">Task</div>
        <div className="whitespace-pre-wrap">{entry.text}</div>
      </div>
    );
  }
  return <div className="whitespace-pre-wrap text-sm leading-relaxed">{entry.text}</div>;
}

interface PauseResumeButtonProps {
  task: SubAgentTask;
  busy: boolean;
  onToggle: (task: SubAgentTask) => void;
  compact?: boolean;
}

function PauseResumeButton({ task, busy, onToggle, compact }: PauseResumeButtonProps) {
  if (task.status !== "running" && task.status !== "paused") return null;
  const label = task.status === "running" ? "Pause" : "Resume";
  return (
    <button
      type="button"
      disabled={busy}
      title={label}
      className={
        compact
          ? "shrink-0 rounded-md border border-[var(--border)] p-1 hover:bg-[var(--panel-bg)] disabled:opacity-40"
          : "flex shrink-0 items-center gap-1 rounded-md border border-[var(--border)] px-2 py-1 text-xs font-medium hover:bg-[var(--panel-bg)] disabled:opacity-40"
      }
      onClick={(e) => {
        e.stopPropagation();
        onToggle(task);
      }}
    >
      {task.status === "running" ? <PauseIcon className="h-3 w-3" /> : <PlayIcon className="h-3 w-3" />}
      {!compact && label}
    </button>
  );
}

interface SubAgentsPanelProps {
  threadId: string;
  onClose: () => void;
}

/** Side panel next to the header's Browser toggle -- observes spawn_agent_
 * background runs (ROADMAP.md's Sub Agents feature): a list of delegated
 * sub-agents with live status, click one to see its full step-by-step
 * transcript (what it did + what it said), pause/resume a running one.
 * Deliberately post-hoc, not a live token stream, per the user's own
 * explicit "跑完后看完整记录即可" bar -- see web/session.py's
 * notify_resync and tools/subagent_tasks.py's module docstrings for the
 * backend design this renders. */
export function SubAgentsPanel({ threadId, onClose }: SubAgentsPanelProps) {
  const [tasks, setTasks] = useState<SubAgentTask[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<SubAgentTask | null>(null);
  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = () => {
      getSubAgentTasks(threadId).then((list) => {
        if (!cancelled) setTasks(list);
      });
    };
    refresh();
    const timer = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [threadId]);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    const refresh = () => {
      getSubAgentTranscript(selectedId).then((res) => {
        if (cancelled) return;
        setEntries(res.entries);
        setSelectedTask(res.task);
      });
    };
    refresh();
    const timer = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [selectedId]);

  const togglePause = async (task: SubAgentTask) => {
    setBusyTaskId(task.task_id);
    try {
      if (task.status === "running") await pauseSubAgentTask(task.task_id);
      else if (task.status === "paused") await resumeSubAgentTask(task.task_id);
      const list = await getSubAgentTasks(threadId);
      setTasks(list);
      if (selectedId === task.task_id) {
        const refreshed = list.find((t) => t.task_id === task.task_id);
        if (refreshed) setSelectedTask(refreshed);
      }
    } finally {
      setBusyTaskId(null);
    }
  };

  return (
    <div className="flex h-full w-[420px] shrink-0 flex-col border-l border-[var(--border)] bg-[var(--panel-bg)]">
      <div className="flex items-center gap-2 border-b border-[var(--border)] px-3 py-2.5">
        {selectedId && (
          <button
            type="button"
            className="text-[var(--muted)] hover:text-[var(--fg)]"
            title="Back to list"
            onClick={() => setSelectedId(null)}
          >
            <ArrowLeftIcon className="h-4 w-4" />
          </button>
        )}
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          {selectedId ? (selectedTask?.description ?? "Sub agent") : "Sub Agents"}
        </span>
        <button type="button" className="text-[var(--muted)] hover:text-[var(--fg)]" title="Close" onClick={onClose}>
          <CloseIcon className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {selectedId ? (
          selectedTask && (
            <div className="flex flex-col gap-3 p-3">
              <div className="flex items-center justify-between gap-2 rounded-lg bg-[var(--card-bg)] p-3">
                <div className="min-w-0">
                  <div className="mb-1 truncate text-xs text-[var(--muted)]">{selectedTask.instructions}</div>
                  <StatusBadge status={selectedTask.status} />
                </div>
                <PauseResumeButton task={selectedTask} busy={busyTaskId === selectedTask.task_id} onToggle={togglePause} />
              </div>

              {entries.map((entry, index) => (
                <TranscriptEntryView key={index} entry={entry} />
              ))}

              {selectedTask.status === "failed" && selectedTask.error && (
                <div className="rounded-lg bg-[var(--danger)]/10 p-3 text-sm text-[var(--danger)]">
                  {selectedTask.error}
                </div>
              )}
              {selectedTask.status === "blocked_on_approval" && (
                <div className="rounded-lg bg-yellow-500/10 p-3 text-sm text-yellow-700 dark:text-yellow-300">
                  This sub-agent is waiting for an approval it can't ask for in the background.
                  Its progress is saved -- give it approval-free tools next time, or delegate the
                  rest of this task in the main conversation instead.
                </div>
              )}
            </div>
          )
        ) : (
          <div className="flex flex-col">
            {tasks.length === 0 && (
              <div className="p-6 text-center text-sm text-[var(--muted)]">
                No sub-agents delegated in this conversation yet.
              </div>
            )}
            {tasks
              .slice()
              .reverse()
              .map((task) => (
                <div
                  key={task.task_id}
                  role="button"
                  tabIndex={0}
                  className="flex flex-col gap-1 border-b border-[var(--border)] px-3 py-3 text-left hover:bg-[var(--card-bg)]"
                  onClick={() => setSelectedId(task.task_id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") setSelectedId(task.task_id);
                  }}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="min-w-0 truncate text-sm font-medium">{task.description}</span>
                    <StatusBadge status={task.status} />
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-xs text-[var(--muted)]">
                      {task.finished_at
                        ? `Finished ${relativeTime(task.finished_at)}`
                        : `Started ${relativeTime(task.started_at)}`}
                    </span>
                    <PauseResumeButton task={task} busy={busyTaskId === task.task_id} onToggle={togglePause} compact />
                  </div>
                </div>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}
