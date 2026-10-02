import { STATUS_LABEL } from "../lib/threadStatus";
import type { ThreadStatus } from "../types/session";
import { EyeIcon, HandIcon } from "./icons";

/** One glyph per conversation status: a plain circle at rest, a spinner
 * while it works, a hand when it waits on you, an eye when its reply is
 * ready to read. */
export function ThreadStatusIcon({ status, className = "h-4 w-4" }: { status: ThreadStatus; className?: string }) {
  const label = STATUS_LABEL[status];
  if (status === "working") {
    return (
      <span
        role="img"
        aria-label={label}
        className={`${className} inline-block shrink-0 animate-spin rounded-full border-[2px] border-[color-mix(in_srgb,var(--fg)_18%,transparent)] border-t-[var(--fg)] motion-reduce:animate-none`}
      />
    );
  }
  if (status === "needs_input") {
    return <HandIcon role="img" aria-label={label} className={`${className} shrink-0`} style={{ color: "var(--warning)" }} />;
  }
  if (status === "ready") {
    return <EyeIcon role="img" aria-label={label} className={`${className} shrink-0`} style={{ color: "var(--accent)" }} />;
  }
  return (
    <span className={`${className} flex shrink-0 items-center justify-center`} role="img" aria-label={label}>
      <span className="h-2.5 w-2.5 rounded-full border-[1.5px] border-[var(--muted)] opacity-70" />
    </span>
  );
}
