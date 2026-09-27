import { lazy, Suspense, useState } from "react";
import { ChevronRightIcon, SubAgentsIcon } from "./icons";

const Markdown = lazy(() => import("./Markdown").then((m) => ({ default: m.Markdown })));

// Mirrors runtime_lg/subagents.py's subagent_report.
export const SUBAGENT_REPORT_PREFIX = "[Sub-agent finished]";
const HEADER_RE = /^\[Sub-agent finished\] "(.*?)" \(task (\w+), (.*?)\) (finished\. Its report:|failed:)\s*/s;

interface ParsedReport {
  description: string;
  model: string;
  failed: boolean;
  body: string;
}

function parseReport(text: string): ParsedReport | null {
  const match = HEADER_RE.exec(text);
  if (!match) return null;
  return {
    description: match[1],
    model: match[3],
    failed: match[4] === "failed:",
    body: text.slice(match[0].length).trim(),
  };
}

/** Stands in for the message a background sub-agent's end sends the
 * conversation -- the app sent it, not the user. */
export function SubAgentReportCard({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const parsed = parseReport(text);
  const model = parsed?.model.split(":").pop();
  return (
    <div className="rounded-xl border border-[var(--border)]">
      <button
        type="button"
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 rounded-xl px-4 py-2.5 text-left text-sm hover:bg-[var(--card-bg)]"
        onClick={() => setOpen((v) => !v)}
      >
        <SubAgentsIcon className="h-4 w-4 shrink-0 text-[var(--muted)]" />
        <span className="min-w-0 flex-1 truncate">
          {parsed ? (
            <>
              Sub-agent {parsed.failed ? "failed" : "finished"}:{" "}
              <span className="text-[var(--muted)]">{parsed.description}</span>
            </>
          ) : (
            "Sub-agent finished"
          )}
        </span>
        {parsed?.failed && <span className="shrink-0 text-xs text-[var(--danger)]">Failed</span>}
        {model && <span className="shrink-0 text-xs text-[var(--muted)]">{model}</span>}
        <ChevronRightIcon
          className={`h-4 w-4 shrink-0 text-[var(--muted)] transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`}
        />
      </button>
      {open && (
        <div className="max-h-96 overflow-y-auto border-t border-[var(--border)] px-4 py-3 text-sm [overflow-wrap:anywhere]">
          {parsed?.failed ? (
            <div className="whitespace-pre-wrap text-[var(--danger)]">{parsed.body}</div>
          ) : (
            <Suspense fallback={<div className="whitespace-pre-wrap">{parsed?.body || text}</div>}>
              <Markdown text={parsed?.body || text} />
            </Suspense>
          )}
        </div>
      )}
    </div>
  );
}
