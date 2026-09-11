import { useRef, useState } from "react";
import { useClickOutside } from "../lib/useClickOutside";
import { formatTokenCount } from "../lib/format";

interface ContextRingProps {
  totalTokens: number;
  contextWindow: number;
  /** null when the provider hasn't reported prompt-cache stats yet this
   * session -- see reducer.ts's own comment on why that stays distinct
   * from "reported, zero tokens cached." */
  cacheStats: { cacheReadTokens: number; inputTokens: number; hitRate: number } | null;
}

const RADIUS = 9;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/** Compact context-window indicator for the composer's model-picker row --
 * replaces the old full-width UsageBar. Click to expand the exact numbers;
 * collapsed, it's just a small ring (used portion in --accent), the
 * rightmost element in that row (past ModelPicker and the Send/Stop
 * button), same popover idiom ModePill/ModelPicker already use
 * (useClickOutside + an absolutely-positioned panel opening upward).
 * Opens right-aligned (`right-0`) since it sits flush against the
 * composer's own right edge -- a left-aligned panel would run off it. */
export function ContextRing({ totalTokens, contextWindow, cacheStats }: ContextRingProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useClickOutside(rootRef, () => setOpen(false), open);

  if (!contextWindow || totalTokens === 0) return null;
  const percent = Math.min(100, (totalTokens / contextWindow) * 100);
  const offset = CIRCUMFERENCE * (1 - percent / 100);

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        title="Context window"
        className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
        onClick={() => setOpen((v) => !v)}
      >
        <svg viewBox="0 0 24 24" className="h-5 w-5 -rotate-90">
          <circle cx="12" cy="12" r={RADIUS} fill="none" stroke="var(--border)" strokeWidth="3" />
          <circle
            cx="12"
            cy="12"
            r={RADIUS}
            fill="none"
            stroke="var(--accent)"
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray={CIRCUMFERENCE}
            strokeDashoffset={offset}
          />
        </svg>
      </button>
      {open && (
        <div className="absolute bottom-full right-0 mb-1 min-w-48 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] p-3 text-sm shadow-[var(--shadow)]">
          <div className="mb-1.5 flex items-center justify-between gap-3">
            <span className="text-[var(--muted)]">Context window</span>
            <span>
              {formatTokenCount(totalTokens)} / {formatTokenCount(contextWindow)} ({percent.toFixed(0)}%)
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-[var(--border)]">
            <div className="h-full bg-[var(--accent)]" style={{ width: `${percent}%` }} />
          </div>
          {cacheStats && (
            <div className="mt-1.5 flex items-center justify-between gap-3 border-t border-[var(--border)] pt-1.5">
              <span className="text-[var(--muted)]">Cache hit (last call)</span>
              <span>
                {formatTokenCount(cacheStats.cacheReadTokens)} / {formatTokenCount(cacheStats.inputTokens)} (
                {(cacheStats.hitRate * 100).toFixed(0)}%)
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
