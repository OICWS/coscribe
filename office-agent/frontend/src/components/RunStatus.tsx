import { useEffect, useState } from "react";
import { formatElapsed, formatTokenCount } from "../lib/format";

interface RunStatusProps {
  turnInFlight: boolean;
  totalTokens: number;
}

/** Live "2m 5s · 1.2k tokens" line shown in the composer while a turn is
 * running -- elapsed time since this turn started, and how much
 * state.totalTokens (the same running total ContextRing shows) has grown
 * by since then. Both numbers tick/update live: the backend now sends a
 * "usage" WS event after every model response within a turn (see
 * web/session.py's _capture_segment_usage), not only once the whole turn
 * finishes, so a long multi-tool-call turn updates the token count as it
 * goes instead of jumping once at the end.
 *
 * Deliberately just the two numbers for now, not the expandable
 * "Editing Fundamentals.md ▾" running action list Claude Code's own
 * status line also shows -- that needs a live list of in-progress
 * actions coscribe doesn't track yet, a separate, larger piece of work. */
export function RunStatus({ turnInFlight, totalTokens }: RunStatusProps) {
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [startTokens, setStartTokens] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!turnInFlight) {
      setStartedAt(null);
      return;
    }
    const start = Date.now();
    setStartedAt(start);
    setStartTokens(totalTokens);
    setNow(start);
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
    // Deliberately keyed only on turnInFlight -- this should reset once
    // per turn (the false -> true transition), not re-run every time
    // totalTokens ticks during the turn it's already timing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [turnInFlight]);

  if (!turnInFlight || startedAt === null) return null;
  const tokenDelta = Math.max(0, totalTokens - startTokens);

  return (
    <span className="text-xs tabular-nums text-[var(--muted)]" data-testid="run-status">
      {formatElapsed(now - startedAt)}
      {tokenDelta > 0 && <> · {formatTokenCount(tokenDelta)} tokens</>}
    </span>
  );
}
