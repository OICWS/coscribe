import { type ReactNode, useEffect, useRef, useState } from "react";
import { formatElapsed, formatTokenCount } from "../lib/format";
import { clearFinishedSubAgents, getSubAgentTasks, getSubAgentTranscript, stopSubAgentTask } from "../lib/rest";
import { summarizeItemParts } from "../lib/transcriptGrouping";
import { historyToItems, type LogItem } from "../state/reducer";
import type { SubAgentTask } from "../types/session";
import { TranscriptItems } from "./ChatLog";
import {
  AlertCircleIcon,
  ArrowLeftIcon,
  CheckCircleIcon,
  ChevronRightIcon,
  CloseIcon,
  StopIcon,
  TrashIcon,
  XCircleIcon,
} from "./icons";

// A fallback: a running session nudges the panel with "subagents_changed".
const POLL_INTERVAL_MS = 5000;

const isActive = (task: SubAgentTask) => task.status === "running" || task.status === "needs_approval";

function modelLabel(model: string): string {
  return model.includes(":") ? model.split(":", 2)[1] : model;
}

/** What it's doing now, in a word or two: "Writing", "Searching the web". */
function activity(task: SubAgentTask): string | null {
  if (task.status === "needs_approval") return null;
  if (!task.last_tool) return "Thinking";
  const parts = summarizeItemParts(
    { id: task.task_id, kind: "tool", toolName: task.last_tool.tool_name, arguments: task.last_tool.arguments },
    true,
  );
  return parts.verb;
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

function Facts({ task, children }: { task: SubAgentTask; children?: ReactNode }) {
  const facts = [
    modelLabel(task.model),
    `${formatTokenCount(task.tokens)} tokens`,
    `${task.tool_uses} tool ${task.tool_uses === 1 ? "use" : "uses"}`,
  ].filter(Boolean);
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-[13px] text-[var(--muted)]">
      {facts.map((fact) => (
        <span key={fact} className="tabular-nums">
          {fact}
        </span>
      ))}
      {children}
    </div>
  );
}

function StopButton({ onStop, busy }: { onStop: () => void; busy: boolean }) {
  return (
    <button
      type="button"
      title="Stop"
      aria-label="Stop"
      disabled={busy}
      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-[var(--border)] bg-[var(--bg)] text-[var(--fg)] hover:bg-[var(--panel-bg)] disabled:opacity-40"
      onClick={(event) => {
        event.stopPropagation();
        onStop();
      }}
    >
      <StopIcon className="h-3 w-3" />
    </button>
  );
}

function RunningCard({
  task,
  now,
  busy,
  onStop,
  onOpen,
}: {
  task: SubAgentTask;
  now: number;
  busy: boolean;
  onStop: () => void;
  onOpen: () => void;
}) {
  const doing = activity(task);
  return (
    <div className="flex flex-col gap-1.5 rounded-xl bg-[var(--card-bg)] px-3.5 py-3">
      <div className="flex items-start justify-between gap-3">
        <span className="min-w-0 text-[15px] leading-snug">{task.description}</span>
        <StopButton onStop={onStop} busy={busy} />
      </div>
      <div className="flex items-center gap-2 text-[13px] text-[var(--muted)]">
        <span>Agent</span>
        <span className="tabular-nums">{formatElapsed(now - new Date(task.started_at).getTime())}</span>
        {task.status === "needs_approval" && (
          <span className="flex items-center gap-1 font-medium text-[var(--warning)]">
            <AlertCircleIcon className="h-3.5 w-3.5" />
            Needs your approval
          </span>
        )}
      </div>
      <Facts task={task}>
        {doing && <span className="shimmer-text">{doing}</span>}
        <button type="button" className="text-[var(--accent)] hover:underline" onClick={onOpen}>
          {task.status === "needs_approval" ? "Review" : "View transcript"}
        </button>
      </Facts>
    </div>
  );
}

function FinishedRow({ task, onOpen }: { task: SubAgentTask; onOpen: () => void }) {
  const icon =
    task.status === "succeeded" ? (
      <CheckCircleIcon className="h-3.5 w-3.5 text-[var(--muted)]" />
    ) : task.status === "failed" ? (
      <XCircleIcon className="h-3.5 w-3.5 text-[var(--danger)]" />
    ) : (
      <StopIcon className="h-3.5 w-3.5 text-[var(--muted)]" />
    );
  return (
    <button
      type="button"
      className="flex w-full min-w-0 flex-col gap-1 rounded-xl px-3.5 py-2.5 text-left hover:bg-[var(--card-bg)]"
      onClick={onOpen}
    >
      <span className="flex w-full min-w-0 items-center gap-2">
        {icon}
        <span className="min-w-0 flex-1 truncate text-sm">{task.description}</span>
        <span className="shrink-0 text-xs text-[var(--muted)]">
          {relativeTime(task.finished_at ?? task.started_at)}
        </span>
      </span>
      <span className="pl-[22px]">
        <Facts task={task} />
      </span>
    </button>
  );
}

/** The delegated prompt, folded to a few lines until asked for. */
function PromptBox({ prompt }: { prompt: string }) {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) setOverflows(el.scrollHeight > el.clientHeight + 2);
  }, [prompt]);
  return (
    <div className="rounded-xl bg-[var(--card-bg)] px-4 py-3 text-sm leading-relaxed">
      <div
        ref={ref}
        className={`relative whitespace-pre-wrap [overflow-wrap:anywhere] ${expanded ? "" : "max-h-40 overflow-hidden"}`}
      >
        {prompt}
        {!expanded && overflows && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-[var(--card-bg)] to-transparent" />
        )}
      </div>
      {(overflows || expanded) && (
        <button
          type="button"
          className="mt-2 text-[13px] text-[var(--muted)] hover:text-[var(--fg)]"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}

function approvalItem(task: SubAgentTask): LogItem | null {
  const pending = task.pending_approval;
  if (!pending) return null;
  return {
    id: pending.id,
    kind: "approval",
    toolName: pending.tool_name,
    arguments: pending.arguments,
    status: "pending",
    beforePreview: pending.before_preview,
    afterPreview: pending.after_preview,
  };
}

function SubAgentDetail({
  taskId,
  refreshKey,
  busy,
  onStop,
  onApprove,
}: {
  taskId: string;
  refreshKey: number;
  busy: boolean;
  onStop: (task: SubAgentTask) => void;
  onApprove: (id: string, approved: boolean) => void;
}) {
  const [task, setTask] = useState<SubAgentTask | null>(null);
  const [items, setItems] = useState<LogItem[]>([]);
  const [answered, setAnswered] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const refresh = () =>
      getSubAgentTranscript(taskId).then((res) => {
        if (cancelled || !res.task) return;
        setTask(res.task);
        setItems(historyToItems(res.entries).filter((item) => item.kind !== "user"));
      });
    refresh();
    const timer = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [taskId, refreshKey]);

  if (!task) return null;
  const pending = approvalItem(task);
  const shown = pending && pending.id !== answered ? [...items, pending] : items;
  return (
    <div className="flex flex-col gap-4 px-4 pb-6 pt-3">
      <div className="flex items-center justify-between gap-3 text-[13px] text-[var(--muted)]">
        <span>
          Model <span className="text-[var(--fg)]">{task.model}</span>
          {task.background ? " · in the background" : ""}
        </span>
        {isActive(task) && <StopButton onStop={() => onStop(task)} busy={busy} />}
      </div>
      <PromptBox prompt={task.prompt} />
      {shown.length === 0 && isActive(task) && <p className="shimmer-text text-sm">Starting</p>}
      <TranscriptItems
        items={shown}
        live={isActive(task)}
        onApprove={(id, approved) => {
          setAnswered(id);
          onApprove(id, approved);
        }}
      />
      {task.status === "failed" && task.error && (
        <p
          role="alert"
          className="rounded-lg bg-[color-mix(in_srgb,var(--danger)_10%,transparent)] px-3 py-2 text-sm text-[var(--danger)]"
        >
          {task.error}
        </p>
      )}
      {task.status === "stopped" && <p className="text-sm text-[var(--muted)]">Stopped before it finished.</p>}
    </div>
  );
}

interface SubAgentsPanelProps {
  threadId: string;
  /** Bumped when the session reports a change, to refresh now. */
  refreshKey: number;
  /** A run to open, e.g. one that just asked for approval. */
  focusTaskId: string | null;
  onApprove: (id: string, approved: boolean) => void;
  onClose: () => void;
}

/** This conversation's sub-agents: the running ones as cards (what each
 * is doing, its model, tokens and tool uses, a Stop button), the finished
 * ones folded away; open one for its prompt and transcript, drawn like the
 * chat, where its approvals are answered. */
export function SubAgentsPanel({ threadId, refreshKey, focusTaskId, onApprove, onClose }: SubAgentsPanelProps) {
  const [tasks, setTasks] = useState<SubAgentTask[]>([]);
  const [openId, setOpenId] = useState<string | null>(focusTaskId);
  const [showFinished, setShowFinished] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (focusTaskId) setOpenId(focusTaskId);
  }, [focusTaskId]);

  useEffect(() => {
    let cancelled = false;
    const refresh = () =>
      getSubAgentTasks(threadId).then((list) => {
        if (!cancelled) setTasks(list);
      });
    refresh();
    const timer = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [threadId, refreshKey]);

  const running = tasks.filter(isActive).reverse();
  const finished = tasks.filter((task) => !isActive(task)).reverse();

  useEffect(() => {
    if (running.length === 0) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running.length]);

  const stop = async (task: SubAgentTask) => {
    setBusyId(task.task_id);
    try {
      await stopSubAgentTask(task.task_id);
      setTasks(await getSubAgentTasks(threadId));
    } finally {
      setBusyId(null);
    }
  };

  const clearFinished = async () => {
    await clearFinishedSubAgents(threadId);
    setTasks(await getSubAgentTasks(threadId));
  };

  const opened = openId ? tasks.find((task) => task.task_id === openId) : undefined;

  return (
    <aside
      aria-label="Sub agents"
      className="flex h-full w-[460px] shrink-0 flex-col border-l border-[var(--border)] bg-[var(--bg)] max-md:w-full"
    >
      <div className="flex h-12 shrink-0 items-center gap-2 px-3">
        {openId && (
          <button
            type="button"
            title="All sub-agents"
            aria-label="All sub-agents"
            className="flex h-7 w-7 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
            onClick={() => setOpenId(null)}
          >
            <ArrowLeftIcon className="h-4 w-4" />
          </button>
        )}
        <span className="min-w-0 flex-1 truncate px-1 text-[15px]">
          {openId ? (opened?.description ?? "Sub-agent") : "Sub agents"}
        </span>
        <button
          type="button"
          title="Close"
          aria-label="Close"
          className="flex h-7 w-7 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
          onClick={onClose}
        >
          <CloseIcon className="h-4 w-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {openId ? (
          <SubAgentDetail
            taskId={openId}
            refreshKey={refreshKey}
            busy={busyId === openId}
            onStop={stop}
            onApprove={onApprove}
          />
        ) : (
          <div className="flex flex-col gap-2 px-3 pb-4">
            {tasks.length === 0 && (
              <p className="px-3 py-8 text-center text-sm text-[var(--muted)]">
                Sub-agents this conversation delegates to show up here.
              </p>
            )}
            {running.length > 0 && (
              <>
                <h3 className="px-1 pt-1 text-sm text-[var(--muted)]">Running</h3>
                {running.map((task) => (
                  <RunningCard
                    key={task.task_id}
                    task={task}
                    now={now}
                    busy={busyId === task.task_id}
                    onStop={() => stop(task)}
                    onOpen={() => setOpenId(task.task_id)}
                  />
                ))}
              </>
            )}
            {finished.length > 0 && (
              <div className="mt-1 flex flex-col">
                <div className="flex items-center justify-between gap-2">
                  <button
                    type="button"
                    aria-expanded={showFinished}
                    className="flex items-center gap-1 rounded-md px-1 py-1 text-sm text-[var(--muted)] hover:text-[var(--fg)]"
                    onClick={() => setShowFinished((v) => !v)}
                  >
                    Finished {finished.length}
                    <ChevronRightIcon
                      className={`h-3.5 w-3.5 transition-transform motion-reduce:transition-none ${showFinished ? "rotate-90" : ""}`}
                    />
                  </button>
                  <button
                    type="button"
                    title="Clear finished"
                    aria-label="Clear finished"
                    className="flex h-7 w-7 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
                    onClick={clearFinished}
                  >
                    <TrashIcon className="h-4 w-4" />
                  </button>
                </div>
                {showFinished &&
                  finished.map((task) => (
                    <FinishedRow key={task.task_id} task={task} onOpen={() => setOpenId(task.task_id)} />
                  ))}
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
