import type { NodeStatus } from "../../lib/workflowRunMap";
import { CheckIcon, ClockIcon, CloseIcon } from "../icons";

/** A step's state in a run, as a small round mark: in the run's list and on
 * its canvas. */
export function StatusDot({ status }: { status: NodeStatus }) {
  const base = "flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full";
  const tint = (color: string) => ({ color, backgroundColor: `color-mix(in srgb, ${color} 16%, transparent)` });
  if (status === "done") {
    return (
      <span className={base} style={tint("var(--success)")} role="img" aria-label="Done">
        <CheckIcon className="h-3 w-3" strokeWidth={3} />
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className={base} style={tint("var(--danger)")} role="img" aria-label="Failed">
        <CloseIcon className="h-3 w-3" strokeWidth={3} />
      </span>
    );
  }
  if (status === "waiting") {
    return (
      <span className={base} style={tint("var(--warning)")} role="img" aria-label="Waiting for you">
        <ClockIcon className="h-3 w-3" strokeWidth={2.5} />
      </span>
    );
  }
  if (status === "stopped") {
    return (
      <span className={`${base} bg-[var(--card-bg)] text-[var(--muted)]`} role="img" aria-label="Stopped">
        <span className="h-2 w-2 rounded-[2px] bg-current" />
      </span>
    );
  }
  if (status === "running") {
    return (
      <span className={`${base} border border-[var(--border-hover)]`} role="img" aria-label="Running">
        <span className="h-3 w-3 animate-spin rounded-full border-[1.5px] border-[var(--border)] border-t-[var(--accent)] motion-reduce:animate-none" />
      </span>
    );
  }
  if (status === "skipped") {
    return (
      <span className={`${base} bg-[var(--card-bg)] text-[var(--muted)]`} role="img" aria-label="Skipped">
        <span className="h-[1.5px] w-2 rounded bg-current" />
      </span>
    );
  }
  return (
    <span
      className={`${base} border-[1.5px] border-dashed border-[var(--border-hover)]`}
      role="img"
      aria-label={status === "pending" ? "Up next" : status === "not_taken" ? "Not taken" : "Not reached"}
    />
  );
}
