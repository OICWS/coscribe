import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  deleteScheduledTask,
  getTaskNotes,
  pauseScheduledTask,
  resumeScheduledTask,
  saveTaskNotes,
  updateScheduledTask,
} from "../lib/rest";
import { goToThread } from "../lib/nav";
import { capitalize, formatRunTime, RUN_STATUS_LABEL, runDuration, runSourceLabel } from "../lib/runLabels";
import { describeSchedule } from "../lib/scheduleLabels";
import type { ScheduledTask } from "../types/settings";
import { ConfirmDialog } from "./ConfirmDialog";
import { ChevronRightIcon, PencilIcon, PlayIcon, TrashIcon } from "./icons";
import { RunStatusIcon } from "./RunStatusIcon";
import { ToggleSwitch } from "./ToggleSwitch";

// Mirrors tools/scheduled_tasks.py's MAX_NOTES_CHARS.
const MAX_NOTES_CHARS = 4000;

const APPROVAL_LABEL: Record<ScheduledTask["approval_mode"], string> = {
  manual: "Ask before every action",
  auto: "Automatically approve",
  skip: "Never ask",
};

const APPROVAL_DESCRIPTION: Record<ScheduledTask["approval_mode"], string> = {
  manual: "coscribe pauses and waits for your approval before any action that needs it.",
  auto: "coscribe runs on its own and uses connectors without pausing for approval.",
  skip: "coscribe never pauses for approval, even for actions that would otherwise need a second look.",
};

function formatNextRun(task: ScheduledTask): string {
  if (task.schedule.kind === "manual") return "Runs when you start it";
  if (!task.next_run_at) return "Not scheduled";
  return `Next run: ${new Date(task.next_run_at).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })}`;
}

function SectionLabel({ children }: { children: ReactNode }) {
  return <div className="mb-1 text-sm text-[var(--muted)]">{children}</div>;
}

const INSTRUCTIONS_COLLAPSED_LINES = 10;

/** Model-drafted instructions run long; collapsed past a screenful so
 * Notes and Runs stay in view. */
function InstructionsSection({ prompt }: { prompt: string }) {
  const [expanded, setExpanded] = useState(false);
  const long = prompt.split("\n").length > INSTRUCTIONS_COLLAPSED_LINES || prompt.length > 900;
  return (
    <div>
      <SectionLabel>Instructions</SectionLabel>
      <div className="relative">
        <div className={`whitespace-pre-wrap text-sm ${long && !expanded ? "max-h-60 overflow-hidden" : ""}`}>
          {prompt}
        </div>
        {long && !expanded && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-[var(--bg)] to-transparent" />
        )}
      </div>
      {long && (
        <button
          type="button"
          className="mt-1 text-sm text-[var(--muted)] hover:text-[var(--fg)] hover:underline"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "Show less" : "Show all"}
        </button>
      )}
    </div>
  );
}

type NotesState = "idle" | "saving" | "saved" | "error";

/** The task's memory across runs: shown to every run at its start and
 * rewritten by the run itself, but also the user's to read and correct.
 * Saved on blur rather than with a button -- it's a scratchpad, not a
 * form. */
function NotesSection({ task, onChanged }: { task: ScheduledTask; onChanged: () => void }) {
  const [notes, setNotes] = useState("");
  const savedNotesRef = useRef("");
  const [status, setStatus] = useState<NotesState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [focused, setFocused] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Re-read after every run too: the run itself rewrites the notes.
  const lastRunFinishedAt = task.runs[task.runs.length - 1]?.finished_at ?? null;
  useEffect(() => {
    if (focused) return;
    getTaskNotes(task.trigger_id).then((result) => {
      if ("notes" in result) {
        setNotes(result.notes);
        savedNotesRef.current = result.notes;
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.trigger_id, lastRunFinishedAt]);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [notes]);

  const save = async () => {
    setFocused(false);
    if (notes.trim() === savedNotesRef.current.trim()) return;
    setStatus("saving");
    const result = await saveTaskNotes(task.trigger_id, notes);
    if ("error" in result) {
      setStatus("error");
      setError(result.error);
      return;
    }
    savedNotesRef.current = result.notes;
    setNotes(result.notes);
    setError(null);
    setStatus("saved");
  };

  const toggleNotes = async () => {
    await updateScheduledTask(task.trigger_id, {
      name: task.name,
      kind: task.schedule.kind,
      at: task.schedule.at,
      prompt: task.prompt,
      ...(task.schedule.weekday !== null ? { weekday: task.schedule.weekday } : {}),
      ...(task.schedule.day_of_month !== null ? { day_of_month: task.schedule.day_of_month } : {}),
      ...(task.schedule.start_date ? { start_date: task.schedule.start_date } : {}),
      ...(task.model ? { model: task.model } : {}),
      approval_mode: task.approval_mode,
      notes_enabled: !task.notes_enabled,
    });
    onChanged();
  };

  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-3">
        <span className="text-sm text-[var(--muted)]">Notes</span>
        <label className="flex cursor-pointer items-center gap-2 text-xs text-[var(--muted)]">
          Remember between runs
          <ToggleSwitch on={task.notes_enabled} onClick={toggleNotes} label="Remember between runs" />
        </label>
      </div>
      <textarea
        ref={textareaRef}
        rows={2}
        value={notes}
        maxLength={MAX_NOTES_CHARS}
        disabled={!task.notes_enabled}
        placeholder={
          task.notes_enabled
            ? "Each run writes what the next one should know here -- progress, where it left off, what changed."
            : "Runs start without notes."
        }
        onFocus={() => {
          setFocused(true);
          setStatus("idle");
        }}
        onBlur={save}
        onChange={(e) => setNotes(e.target.value)}
        className="-mx-2 block w-[calc(100%+1rem)] resize-none overflow-hidden rounded-md border border-transparent bg-transparent px-2 py-1.5 text-sm leading-relaxed outline-none transition-colors placeholder:text-[var(--muted)] hover:border-[var(--border)] focus:border-[var(--border-hover)] disabled:cursor-not-allowed disabled:opacity-50"
      />
      <div className="mt-1 h-4 text-xs text-[var(--muted)]" aria-live="polite">
        {status === "saving" && "Saving…"}
        {status === "saved" && "Saved"}
        {status === "error" && <span className="text-[var(--danger)]">{error}</span>}
        {status === "idle" && focused && `${notes.length} / ${MAX_NOTES_CHARS}`}
      </div>
    </div>
  );
}

function RunsSection({ task }: { task: ScheduledTask }) {
  const runs = [...task.runs].reverse();
  return (
    <div>
      <SectionLabel>Runs</SectionLabel>
      {runs.length === 0 ? (
        <p className="text-sm text-[var(--muted)]">No runs yet -- use Run now to try it out.</p>
      ) : (
        <ul className="-mx-2 flex flex-col">
          {runs.map((run) => {
            const duration = runDuration(run);
            return (
              <li key={run.run_id}>
                <button
                  type="button"
                  className="group flex w-full items-center gap-3 rounded-md px-2 py-2 text-left text-sm hover:bg-[var(--card-bg)]"
                  onClick={() => goToThread(run.thread_id)}
                  title={run.error ?? undefined}
                >
                  <RunStatusIcon status={run.status} />
                  <span className="min-w-0 flex-1 truncate">
                    {capitalize(formatRunTime(run.started_at))}
                    <span className="text-[var(--muted)]">
                      {" "}
                      · {runSourceLabel(run)}
                      {duration && ` · ${duration}`}
                    </span>
                  </span>
                  {run.status !== "completed" && (
                    <span className="shrink-0 text-xs text-[var(--muted)]">{RUN_STATUS_LABEL[run.status]}</span>
                  )}
                  <ChevronRightIcon className="h-4 w-4 shrink-0 text-[var(--muted)] opacity-0 transition-opacity group-hover:opacity-100" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

interface ScheduledTaskDetailProps {
  task: ScheduledTask;
  onEdit: () => void;
  onRunNow: () => void;
  onDeleted: () => void;
  onChanged: () => void;
}

/** One scheduled task: its settings (docs/ui-references/
 * sheduled-display-tasks.png), its notes, and every run it has made --
 * each run opening its own conversation. */
export function ScheduledTaskDetail({ task, onEdit, onRunNow, onDeleted, onChanged }: ScheduledTaskDetailProps) {
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [starting, setStarting] = useState(false);

  const toggleEnabled = async () => {
    if (task.enabled) await pauseScheduledTask(task.trigger_id);
    else await resumeScheduledTask(task.trigger_id);
    onChanged();
  };

  const confirmDelete = async () => {
    setDeleteConfirm(false);
    await deleteScheduledTask(task.trigger_id);
    onDeleted();
  };

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-3xl">
        <div className="flex items-center justify-between gap-4">
          <h1
            className="min-w-0 truncate text-2xl font-semibold"
            style={{ fontFamily: "Georgia, 'Times New Roman', serif" }}
          >
            {task.name}
          </h1>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              aria-label="Edit"
              title="Edit"
              className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
              onClick={onEdit}
            >
              <PencilIcon className="h-4 w-4" />
            </button>
            <button
              type="button"
              aria-label="Delete"
              title="Delete"
              className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--danger)]"
              onClick={() => setDeleteConfirm(true)}
            >
              <TrashIcon className="h-4 w-4" />
            </button>
            <button
              type="button"
              disabled={starting}
              className="flex items-center gap-1.5 rounded-md bg-[var(--primary)] px-3 py-1.5 text-sm font-medium text-[var(--primary-fg)] hover:bg-[var(--primary-hover)] disabled:opacity-60"
              onClick={() => {
                setStarting(true);
                onRunNow();
              }}
            >
              <PlayIcon className="h-3.5 w-3.5" /> Run now
            </button>
          </div>
        </div>

        <div className="mt-2 flex items-center gap-2">
          <ToggleSwitch on={task.enabled} onClick={toggleEnabled} label={task.enabled ? "Pause task" : "Resume task"} />
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-medium ${
              task.enabled
                ? "bg-green-500/15 text-green-600 dark:text-green-400"
                : "bg-[var(--card-bg)] text-[var(--muted)]"
            }`}
          >
            {task.enabled ? "Active" : "Paused"}
          </span>
          <span className="text-sm text-[var(--muted)]">{formatNextRun(task)}</span>
        </div>

        <div className="mt-4 border-t border-[var(--border)]" />

        <div className="mt-6 flex flex-col gap-6">
          <InstructionsSection prompt={task.prompt} />
          <div>
            <SectionLabel>Repeats</SectionLabel>
            <div className="text-sm font-medium">{describeSchedule(task.schedule)}</div>
          </div>
          <div>
            <SectionLabel>Permissions</SectionLabel>
            <div className="text-sm font-medium">{APPROVAL_LABEL[task.approval_mode]}</div>
            <div className="text-sm text-[var(--muted)]">{APPROVAL_DESCRIPTION[task.approval_mode]}</div>
          </div>
          <NotesSection task={task} onChanged={onChanged} />
          <RunsSection task={task} />
        </div>
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
