/** Compact "1.2k"/"3.4M" rendering for a raw token count -- shared by
 * ContextRing (context-window ring) and RunStatus (live per-turn
 * counter), both of which render the same state.totalTokens value at
 * different moments. */
export function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/** "Xm Ys" (or just "Ys" under a minute) rendering for an elapsed
 * duration in milliseconds -- RunStatus's live turn timer. */
export function formatElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}
