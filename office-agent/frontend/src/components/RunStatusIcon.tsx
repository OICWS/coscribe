import { RUN_STATUS_LABEL } from "../lib/runLabels";
import type { RunStatus } from "../types/settings";
import { AlertCircleIcon, CheckCircleIcon, StopIcon, XCircleIcon } from "./icons";

interface RunStatusIconProps {
  status: RunStatus;
  className?: string;
}

/** One small glyph per run status -- shared by the sidebar, portal cards,
 * the detail page's run list and the run switcher, so a status reads the
 * same everywhere. */
export function RunStatusIcon({ status, className = "h-3.5 w-3.5" }: RunStatusIconProps) {
  const label = RUN_STATUS_LABEL[status];
  if (status === "running") {
    return (
      <span
        role="img"
        aria-label={label}
        className={`${className} inline-block shrink-0 animate-spin rounded-full border-[2px] border-[color-mix(in_srgb,var(--fg)_15%,transparent)] border-t-[var(--accent)] motion-reduce:animate-none`}
      />
    );
  }
  const common = { className: `${className} shrink-0`, "aria-label": label, role: "img" };
  if (status === "completed") return <CheckCircleIcon {...common} style={{ color: "var(--success)" }} />;
  if (status === "failed") return <XCircleIcon {...common} style={{ color: "var(--danger)" }} />;
  if (status === "needs_approval") return <AlertCircleIcon {...common} style={{ color: "var(--warning)" }} />;
  return <StopIcon {...common} style={{ color: "var(--muted)" }} />;
}
