import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { getThreadActivity, openThreadFile, threadFileDownloadUrl } from "../lib/rest";
import { formatRunTime, runSourceLabel } from "../lib/runLabels";
import { toolLabel } from "../lib/toolLabels";
import { readStored, writeStored } from "../lib/storage";
import type { ScheduledRun, ScheduledTask } from "../types/settings";
import type { ActivityFile, ActivityToolUse, ThreadActivity } from "../types/session";
import type { ProgressItem, ProgressStatus } from "../lib/workflowProgress";
import { CheckIcon, ChevronDownIcon, ChevronRightIcon, CloseIcon, DownloadIcon, FolderIcon } from "./icons";
import { RunStatusIcon } from "./RunStatusIcon";

type SectionId = "progress" | "outputs" | "context";

const COLLAPSED_KEY = "coscribe.taskPanel.collapsed";
// Coalesces the burst of tool results a single model step can produce.
const REFRESH_DEBOUNCE_MS = 500;

interface TaskPanelProps {
  threadId: string;
  title: string;
  /** Set when this conversation is a scheduled run. */
  task: ScheduledTask | null;
  run: ScheduledRun | null;
  /** A workflow run's steps, shown as Progress in place of the plan. */
  workflowSteps: ProgressItem[] | null;
  /** Changes whenever the conversation may have done something new. */
  refreshSignal: string;
  onOpenTask: (task: ScheduledTask) => void;
}

function readCollapsed(): Set<SectionId> {
  const raw = readStored(COLLAPSED_KEY);
  return new Set(raw ? (raw.split(",").filter(Boolean) as SectionId[]) : []);
}

/** The right-hand panel beside a conversation
 * (docs/ui-references/scheduled-siderbar-task-running.png): what the
 * model planned, which files it produced, and what it drew on. */
export function TaskPanel({ threadId, title, task, run, workflowSteps, refreshSignal, onOpenTask }: TaskPanelProps) {
  const [activity, setActivity] = useState<ThreadActivity | null>(null);
  const [collapsed, setCollapsed] = useState<Set<SectionId>>(readCollapsed);
  const [fileError, setFileError] = useState<string | null>(null);
  const requestRef = useRef(0);

  useEffect(() => {
    setActivity(null);
    setFileError(null);
  }, [threadId]);

  useEffect(() => {
    const request = ++requestRef.current;
    const timer = window.setTimeout(() => {
      getThreadActivity(threadId)
        .then((next) => {
          if (request === requestRef.current) setActivity(next);
        })
        .catch(() => {
          // Keep showing the last good snapshot; the next signal retries.
        });
    }, REFRESH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [threadId, refreshSignal]);

  useEffect(() => {
    if (!fileError) return;
    const timer = window.setTimeout(() => setFileError(null), 6000);
    return () => window.clearTimeout(timer);
  }, [fileError]);

  const toggle = (id: SectionId) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      writeStored(COLLAPSED_KEY, [...next].join(","));
      return next;
    });

  const openFile = useCallback(
    async (file: ActivityFile, reveal: boolean) => {
      setFileError(await openThreadFile(threadId, file.path, reveal));
    },
    [threadId],
  );

  const progressItems: ProgressItem[] | null = workflowSteps ?? (activity?.tasks.length ? activity.tasks : null);

  const contextEmpty =
    activity !== null &&
    activity.references.length === 0 &&
    activity.tools.length === 0 &&
    activity.connectors.length === 0 &&
    activity.skills.length === 0;

  return (
    <aside
      aria-label="Task details"
      className="my-2 mr-2 hidden w-[320px] shrink-0 flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--panel-bg)] lg:flex"
    >
      <div className="px-4 pb-3 pt-3.5">
        {task ? (
          <button
            type="button"
            className="group flex max-w-full items-center gap-1 text-left text-sm font-medium"
            onClick={() => onOpenTask(task)}
            title="Task details"
          >
            <span className="truncate">{task.name}</span>
            <ChevronRightIcon className="h-3.5 w-3.5 shrink-0 text-[var(--muted)] transition-transform group-hover:translate-x-0.5 motion-reduce:transition-none" />
          </button>
        ) : (
          <div className="truncate text-sm font-medium">{title}</div>
        )}
        {run && (
          <div className="mt-2.5 flex items-center gap-2 rounded-lg bg-[var(--card-bg)] px-2.5 py-2 text-[13px]">
            <RunStatusIcon status={run.status} />
            <span className="min-w-0 truncate">
              Run · {formatRunTime(run.started_at)}
              <span className="text-[var(--muted)]"> · {runSourceLabel(run)}</span>
            </span>
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <Section id="progress" title="Progress" collapsed={collapsed} onToggle={toggle}>
          {progressItems ? (
            <ProgressView tasks={progressItems} unit={workflowSteps ? "steps" : undefined} />
          ) : (
            <EmptyState art={<ProgressArt />} text="See task progress for longer tasks." />
          )}
        </Section>

        <Section id="outputs" title="Outputs" count={activity?.outputs.length} collapsed={collapsed} onToggle={toggle}>
          {activity && activity.outputs.length > 0 ? (
            <ul className="-mx-2 flex flex-col">
              {activity.outputs.map((file) => (
                <FileRow key={file.path} file={file} threadId={threadId} onOpen={openFile} />
              ))}
            </ul>
          ) : (
            <EmptyState art={<OutputsArt />} text="View and open files created during this task." />
          )}
          {fileError && (
            <p role="alert" className="mt-2 text-xs text-[var(--danger)]">
              {fileError}
            </p>
          )}
        </Section>

        <Section id="context" title="Context" collapsed={collapsed} onToggle={toggle} last>
          {activity && !contextEmpty ? (
            <ContextView activity={activity} threadId={threadId} onOpen={openFile} />
          ) : (
            <EmptyState art={<ContextArt />} text="Track tools and referenced files used in this task." />
          )}
        </Section>
      </div>
    </aside>
  );
}

function Section({
  id,
  title,
  count,
  collapsed,
  onToggle,
  last,
  children,
}: {
  id: SectionId;
  title: string;
  count?: number;
  collapsed: Set<SectionId>;
  onToggle: (id: SectionId) => void;
  last?: boolean;
  children: ReactNode;
}) {
  const open = !collapsed.has(id);
  return (
    <section className={`border-t border-[var(--border)] px-4 py-3 ${last ? "pb-4" : ""}`}>
      <button
        type="button"
        aria-expanded={open}
        className="flex items-center gap-1.5 rounded text-sm font-medium"
        onClick={() => onToggle(id)}
      >
        {title}
        {count !== undefined && count > 0 && (
          <span className="text-xs font-normal tabular-nums text-[var(--muted)]">{count}</span>
        )}
        <ChevronDownIcon
          className={`h-3.5 w-3.5 text-[var(--muted)] transition-transform motion-reduce:transition-none ${open ? "" : "-rotate-90"}`}
        />
      </button>
      {open && <div className="mt-3">{children}</div>}
    </section>
  );
}

function EmptyState({ art, text }: { art: ReactNode; text: string }) {
  return (
    <div>
      {art}
      <p className="mt-3 text-xs text-[var(--muted)]">{text}</p>
    </div>
  );
}

// -- Progress ---------------------------------------------------------------

const DOT_SIZE = { md: "h-5 w-5", sm: "h-4 w-4" } as const;

function StepDot({ status, size = "md" }: { status: ProgressStatus; size?: keyof typeof DOT_SIZE }) {
  const box = `${DOT_SIZE[size]} flex shrink-0 items-center justify-center rounded-full`;
  if (status === "failed") {
    return (
      <span
        className={box}
        style={{ color: "var(--danger)", backgroundColor: "color-mix(in srgb, var(--danger) 16%, transparent)" }}
      >
        <CloseIcon className={size === "md" ? "h-3 w-3" : "h-2.5 w-2.5"} strokeWidth={3} />
      </span>
    );
  }
  if (status === "completed") {
    return (
      <span className={`${box} border border-[var(--border-hover)] text-[var(--muted)]`}>
        <CheckIcon className={size === "md" ? "h-3 w-3" : "h-2.5 w-2.5"} />
      </span>
    );
  }
  if (status === "in_progress") {
    return (
      <span className={`${box} border border-[var(--border-hover)]`}>
        <span
          className={`${size === "md" ? "h-3 w-3" : "h-2.5 w-2.5"} animate-spin rounded-full border-[1.5px] border-[var(--border)] border-t-[var(--accent)] motion-reduce:animate-none`}
        />
      </span>
    );
  }
  return <span className={`${box} bg-[var(--card-bg)]`} />;
}

const STEP_TEXT_CLASS: Record<ProgressStatus, string> = {
  pending: "",
  in_progress: "font-medium",
  completed: "text-[var(--muted)]",
  failed: "font-medium text-[var(--danger)]",
};

function ProgressView({ tasks, unit }: { tasks: ProgressItem[]; unit?: string }) {
  const done = tasks.filter((t) => t.status === "completed").length;
  const stopped = tasks.some((t) => t.status === "failed");
  return (
    <div>
      <div className="flex flex-wrap items-center gap-y-2" aria-hidden="true">
        {tasks.map((task, index) => (
          <span key={task.id} className="flex items-center">
            {index > 0 && <span className="h-px w-2.5 bg-[var(--border-hover)]" />}
            <StepDot status={task.status} />
          </span>
        ))}
      </div>
      <p className="mt-2.5 text-xs tabular-nums text-[var(--muted)]">
        {done} of {tasks.length} {unit ?? "done"}
        {stopped && " · stopped"}
      </p>
      <ol className="mt-2 flex flex-col gap-1.5">
        {tasks.map((task) => (
          <li key={task.id} className="flex items-start gap-2 text-[13px] leading-5">
            <span className="mt-0.5">
              <StepDot status={task.status} size="sm" />
            </span>
            <span className={STEP_TEXT_CLASS[task.status]}>{task.content}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

// -- Files --------------------------------------------------------------------

const FILE_KINDS: { extensions: string[]; color: string }[] = [
  { extensions: ["xlsx", "xls", "xlsm", "csv"], color: "var(--file-sheet)" },
  { extensions: ["docx", "doc", "md", "txt"], color: "var(--file-doc)" },
  { extensions: ["pptx", "ppt"], color: "var(--file-slides)" },
  { extensions: ["pdf"], color: "var(--file-pdf)" },
  { extensions: ["png", "jpg", "jpeg", "gif", "webp", "svg"], color: "var(--file-image)" },
];

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

function FileBadge({ name, small }: { name: string; small?: boolean }) {
  const ext = extensionOf(name);
  const color = FILE_KINDS.find((k) => k.extensions.includes(ext))?.color ?? "var(--muted)";
  return (
    <span
      aria-hidden="true"
      className={`flex shrink-0 items-center justify-center rounded-md font-semibold uppercase tracking-wide ${small ? "h-6 w-6 text-[8px]" : "h-8 w-8 text-[9px]"}`}
      style={{ color, backgroundColor: `color-mix(in srgb, ${color} 14%, transparent)` }}
    >
      {ext.slice(0, 4) || "file"}
    </span>
  );
}

function folderOf(path: string): string {
  const slash = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return slash > 0 ? path.slice(0, slash) : "";
}

function FileRow({
  file,
  threadId,
  onOpen,
  compact,
}: {
  file: ActivityFile;
  threadId: string;
  onOpen: (file: ActivityFile, reveal: boolean) => void;
  compact?: boolean;
}) {
  const parts = [folderOf(file.path)];
  if (!file.exists) parts.push("No longer on disk");
  else if (file.modified_at && !compact) parts.push(`Edited ${formatRunTime(file.modified_at)}`);
  const subtitle = parts.filter(Boolean).join(" · ");
  const iconButton =
    "flex h-7 w-7 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--bg)] hover:text-[var(--fg)]";
  return (
    <li className="group relative">
      <button
        type="button"
        disabled={!file.exists}
        title={file.openable ? `Open ${file.name}` : `Show ${file.name} in its folder`}
        className="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-[var(--card-bg)] focus-visible:bg-[var(--card-bg)] disabled:opacity-50 disabled:hover:bg-transparent"
        onClick={() => onOpen(file, !file.openable)}
      >
        <FileBadge name={file.name} small={compact} />
        <span className="min-w-0 flex-1 pr-14">
          <span className="block truncate text-[13px]">{file.name}</span>
          {subtitle && <span className="block truncate text-xs text-[var(--muted)]">{subtitle}</span>}
        </span>
      </button>
      {file.exists && (
        <span className="absolute right-1.5 top-1/2 flex -translate-y-1/2 items-center opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 motion-reduce:transition-none">
          <button
            type="button"
            className={iconButton}
            title="Show in folder"
            aria-label={`Show ${file.name} in folder`}
            onClick={() => onOpen(file, true)}
          >
            <FolderIcon className="h-3.5 w-3.5" />
          </button>
          <a
            className={iconButton}
            href={threadFileDownloadUrl(threadId, file.path)}
            download={file.name}
            title="Download"
            aria-label={`Download ${file.name}`}
          >
            <DownloadIcon className="h-3.5 w-3.5" />
          </a>
        </span>
      )}
    </li>
  );
}

// -- Context ------------------------------------------------------------------

function Chip({ label, count }: { label: string; count?: number }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1 rounded-full border border-[var(--border)] px-2 py-0.5 text-xs">
      <span className="truncate">{label}</span>
      {count !== undefined && count > 1 && <span className="tabular-nums text-[var(--muted)]">×{count}</span>}
    </span>
  );
}

function ContextGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-[var(--muted)]">{label}</div>
      {children}
    </div>
  );
}

function ContextView({
  activity,
  threadId,
  onOpen,
}: {
  activity: ThreadActivity;
  threadId: string;
  onOpen: (file: ActivityFile, reveal: boolean) => void;
}) {
  const tools: ActivityToolUse[] = [...activity.tools].sort((a, b) => b.count - a.count);
  return (
    <div className="flex flex-col gap-4">
      {activity.references.length > 0 && (
        <ContextGroup label="Files">
          <ul className="-mx-2 flex flex-col">
            {activity.references.map((file) => (
              <FileRow key={file.path} file={file} threadId={threadId} onOpen={onOpen} compact />
            ))}
          </ul>
        </ContextGroup>
      )}
      {activity.skills.length > 0 && (
        <ContextGroup label="Skills">
          <div className="flex flex-wrap gap-1.5">
            {activity.skills.map((skill) => (
              <Chip key={skill} label={skill} />
            ))}
          </div>
        </ContextGroup>
      )}
      {activity.connectors.length > 0 && (
        <ContextGroup label="Connectors">
          <div className="flex flex-wrap gap-1.5">
            {activity.connectors.map((connector) => (
              <Chip
                key={connector.server}
                label={connector.server}
                count={connector.tools.reduce((sum, t) => sum + t.count, 0)}
              />
            ))}
          </div>
        </ContextGroup>
      )}
      {tools.length > 0 && (
        <ContextGroup label="Tools">
          <div className="flex flex-wrap gap-1.5">
            {tools.map((tool) => (
              <Chip key={tool.name} label={toolLabel(tool.name)} count={tool.count} />
            ))}
          </div>
        </ContextGroup>
      )}
    </div>
  );
}

// -- Empty-state art ------------------------------------------------------------

function ProgressArt() {
  return (
    <div className="flex items-center" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <span key={i} className="flex items-center">
          {i > 0 && <span className="h-px w-2.5 bg-[var(--border-hover)]" />}
          <span
            className={`flex h-6 w-6 items-center justify-center rounded-full ${i < 2 ? "border border-[var(--border-hover)] text-[var(--muted)]" : "bg-[var(--card-bg)]"}`}
          >
            {i < 2 && <CheckIcon className="h-3 w-3" />}
          </span>
        </span>
      ))}
    </div>
  );
}

function OutputsArt() {
  return (
    <svg width="60" height="38" viewBox="0 0 60 38" aria-hidden="true" className="block">
      <rect x="0.5" y="0.5" width="59" height="37" rx="6" fill="var(--card-bg)" stroke="var(--border-hover)" />
      <rect x="18" y="16" width="4" height="12" rx="1" fill="var(--border-hover)" />
      <rect x="25" y="11" width="4" height="17" rx="1" fill="var(--border-hover)" />
      <rect x="32" y="13" width="4" height="15" rx="1" fill="var(--border-hover)" />
      <rect x="39" y="9" width="4" height="19" rx="1" fill="var(--border-hover)" />
    </svg>
  );
}

function ContextArt() {
  const lines = (x: number, y: number) =>
    [0, 1, 2].map((i) => (
      <rect key={i} x={x} y={y + i * 5} width={i === 2 ? 10 : 16} height="1.5" rx="0.75" fill="var(--border-hover)" />
    ));
  return (
    <svg width="112" height="48" viewBox="0 0 112 48" aria-hidden="true" className="block">
      <rect x="0.5" y="16.5" width="32" height="31" rx="5" fill="var(--card-bg)" stroke="var(--border-hover)" />
      {lines(8, 26)}
      <rect x="38.5" y="16.5" width="32" height="31" rx="5" fill="var(--card-bg)" stroke="var(--border-hover)" />
      {lines(46, 26)}
      <rect x="62.5" y="0.5" width="32" height="38" rx="5" fill="var(--bg)" stroke="var(--border-hover)" />
      {lines(70, 9)}
    </svg>
  );
}
