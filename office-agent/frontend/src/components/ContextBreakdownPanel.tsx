import { useEffect, useState } from "react";
import { getContextBreakdown } from "../lib/rest";
import type { ContextBreakdown } from "../types/session";
import { formatTokenCount } from "../lib/format";

/** Fixed categorical order + colors, from this project's dataviz skill's
 * own validated default palette (references/palette.md) -- slots 1-5
 * (blue/orange/aqua/yellow/magenta) assigned in that fixed order, never
 * cycled or reassigned by rank, same rule the skill states for any
 * categorical series. "Autocompact buffer" and "Free space" aren't real
 * *content* categories the way the other five are (nothing was spent on
 * them, they're reserved/unused headroom), so they get neutral/muted
 * tones instead of a 6th/7th saturated hue -- matches the reference
 * screenshot's own visual treatment (those two rows read as distinctly
 * lighter than the colorful "used" ones). */
const CATEGORY_STYLE: Record<string, string> = {
  messages: "bg-[#2a78d6] dark:bg-[#3987e5]",
  system_tools: "bg-[#eb6834] dark:bg-[#d95926]",
  mcp_tools: "bg-[#1baf7a] dark:bg-[#199e70]",
  system_prompt: "bg-[#eda100] dark:bg-[#c98500]",
  skills: "bg-[#e87ba4] dark:bg-[#d55181]",
  autocompact_buffer: "bg-[var(--border)]",
  free_space: "bg-[var(--card-bg)] border border-[var(--border)]",
};
const DEFAULT_STYLE = "bg-[var(--muted)]";

interface ContextBreakdownPanelProps {
  threadId: string;
  /** Re-fetches whenever this changes -- the caller bumps it each time
   * the popover opens, so the breakdown is never more stale than "since
   * the last time you looked at it," without polling while closed. */
  refreshKey: number;
}

export function ContextBreakdownPanel({ threadId, refreshKey }: ContextBreakdownPanelProps) {
  const [breakdown, setBreakdown] = useState<ContextBreakdown | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(false);
    getContextBreakdown(threadId)
      .then((result) => {
        if (!cancelled) setBreakdown(result);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [threadId, refreshKey]);

  if (error) {
    return <div className="mt-2 border-t border-[var(--border)] pt-2 text-xs text-[var(--muted)]">Couldn't load breakdown.</div>;
  }
  if (!breakdown) {
    return <div className="mt-2 border-t border-[var(--border)] pt-2 text-xs text-[var(--muted)]">Loading...</div>;
  }

  const denominator = Math.max(1, breakdown.context_window);

  return (
    <div className="mt-2 border-t border-[var(--border)] pt-2">
      {/* Stacked bar: one segment per category, width proportional to
       * its share of the *whole context window* (not just the used
       * portion), so "free space" visibly reads as free. A 1px gap
       * between segments (the dataviz skill's own spacer rule for
       * adjacent stacked fills) keeps same-ish-width neighbors from
       * reading as one merged block. */}
      <div className="flex h-2 gap-px overflow-hidden rounded-full">
        {breakdown.categories.map((category) => {
          const width = (category.tokens / denominator) * 100;
          if (width <= 0) return null;
          return (
            <div
              key={category.key}
              title={`${category.label}: ${formatTokenCount(category.tokens)}`}
              className={CATEGORY_STYLE[category.key] ?? DEFAULT_STYLE}
              style={{ width: `${width}%` }}
            />
          );
        })}
      </div>

      <div className="mt-2 flex flex-col gap-1">
        {breakdown.categories.map((category) => {
          const percent = (category.tokens / denominator) * 100;
          return (
            <div key={category.key} className="flex items-center gap-2">
              <span className={`h-2.5 w-2.5 shrink-0 rounded-sm ${CATEGORY_STYLE[category.key] ?? DEFAULT_STYLE}`} />
              <span className="min-w-0 flex-1 truncate text-[var(--muted)]">{category.label}</span>
              <span className="shrink-0 tabular-nums">{formatTokenCount(category.tokens)}</span>
              <span className="w-9 shrink-0 text-right tabular-nums text-[var(--muted)]">
                {percent.toFixed(1)}%
              </span>
            </div>
          );
        })}
      </div>

      {breakdown.total_is_estimated && (
        <div className="mt-2 text-[11px] text-[var(--muted)]">
          Estimated -- no real usage reported for this thread yet.
        </div>
      )}
    </div>
  );
}
