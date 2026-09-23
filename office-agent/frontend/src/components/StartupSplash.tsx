/** Same layout and wording as the desktop shell's own splash
 * (office-agent-desktop/src/splash/index.html), which hands off to this
 * page once the server answers -- so the switch between them is
 * invisible and the app only appears once it has real data to show. */
export function StartupSplash() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-5 bg-[var(--bg)] text-[var(--fg)]">
      <div
        className="h-7 w-7 animate-spin rounded-full border-[3px] border-[color-mix(in_srgb,var(--fg)_15%,transparent)] border-t-[var(--accent)] motion-reduce:animate-none"
        role="status"
        aria-label="Loading"
      />
      <div className="text-[15px] font-semibold tracking-[0.01em]">Starting coscribe…</div>
      <div className="min-h-[1.2em] text-[13px] text-[var(--muted)]">Still starting up…</div>
    </div>
  );
}
