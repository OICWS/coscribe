import { useState } from "react";
import { ChevronRightIcon, ClockIcon, SparkIcon } from "./icons";

// Mirrors runtime_lg/scheduled_tasks.py's build_run_prompt and
// build_investigation_prompt.
export const RUN_PROMPT_PREFIX = "[Scheduled run of ";
export const INVESTIGATION_PREFIX = "[Look into a failed run of ";
const INVESTIGATION_HEADER_RE = /^\[Look into a failed run of "(.*?)" ·/;
const NOTES_MARKER = "\n\n---\nNotes from earlier runs of this task:\n";
const NOTES_INSTRUCTIONS_START = "\n\nBefore you finish, call update_task_notes";
const HEADER_RE = /^\[Scheduled run of ".*?" · (started manually|on schedule) · (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\./;

interface ParsedRunPrompt {
  source: string | null;
  startedAt: string | null;
  instructions: string;
  notes: string | null;
}

function parseRunPrompt(text: string): ParsedRunPrompt {
  const headerEnd = text.indexOf("]\n\n");
  const header = headerEnd === -1 ? text : text.slice(0, headerEnd + 1);
  let body = headerEnd === -1 ? "" : text.slice(headerEnd + 3);
  let notes: string | null = null;
  const notesAt = body.indexOf(NOTES_MARKER);
  if (notesAt !== -1) {
    notes = body.slice(notesAt + NOTES_MARKER.length);
    const instructionsAt = notes.indexOf(NOTES_INSTRUCTIONS_START);
    if (instructionsAt !== -1) notes = notes.slice(0, instructionsAt);
    body = body.slice(0, notesAt);
  }
  const match = HEADER_RE.exec(header);
  return {
    source: match ? (match[1] === "started manually" ? "Manual" : "Scheduled") : null,
    startedAt: match
      ? new Date(match[2].replace(" ", "T")).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
      : null,
    instructions: body.trim(),
    notes: notes?.trim() || null,
  };
}

/** Stands in for a scheduled run's own prompt, which the run sent -- not
 * the user. Expands to show what the run was told,
 * including the notes carried over from earlier runs. */
export function ScheduledRunCard({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const parsed = parseRunPrompt(text);
  return (
    <div className="rounded-xl border border-[var(--border)]">
      <button
        type="button"
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 rounded-xl px-4 py-2.5 text-left text-sm hover:bg-[var(--card-bg)]"
        onClick={() => setOpen((v) => !v)}
      >
        <ClockIcon className="h-4 w-4 shrink-0 text-[var(--muted)]" />
        <span className="flex-1">Ran scheduled task</span>
        {parsed.source && (
          <span className="text-xs text-[var(--muted)]">
            {parsed.source}
            {parsed.startedAt && ` · ${parsed.startedAt}`}
          </span>
        )}
        <ChevronRightIcon
          className={`h-4 w-4 shrink-0 text-[var(--muted)] transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`}
        />
      </button>
      {open && (
        <div className="flex flex-col gap-3 border-t border-[var(--border)] px-4 py-3 text-sm">
          <div>
            <div className="mb-1 text-xs font-medium uppercase tracking-wide text-[var(--muted)]">Instructions</div>
            <div className="whitespace-pre-wrap">{parsed.instructions || text}</div>
          </div>
          {parsed.notes && (
            <div>
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
                Notes carried over
              </div>
              <div className="whitespace-pre-wrap text-[var(--muted)]">{parsed.notes}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Stands in for the message that opened a conversation looking into a
 * failed workflow run -- the app wrote it, not the user. */
export function InvestigationCard({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const name = INVESTIGATION_HEADER_RE.exec(text)?.[1] ?? "a task";
  const headerEnd = text.indexOf("]\n\n");
  const body = headerEnd === -1 ? text : text.slice(headerEnd + 3);
  return (
    <div className="rounded-xl border border-[var(--border)]" data-testid="investigation-card">
      <button
        type="button"
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 rounded-xl px-4 py-2.5 text-left text-sm hover:bg-[var(--card-bg)]"
        onClick={() => setOpen((v) => !v)}
      >
        <SparkIcon className="h-4 w-4 shrink-0 text-[var(--muted)]" />
        <span className="min-w-0 flex-1 truncate">Look into the failed run of “{name}”</span>
        <ChevronRightIcon
          className={`h-4 w-4 shrink-0 text-[var(--muted)] transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`}
        />
      </button>
      {open && (
        <div className="whitespace-pre-wrap break-words border-t border-[var(--border)] px-4 py-3 text-sm">{body}</div>
      )}
    </div>
  );
}
