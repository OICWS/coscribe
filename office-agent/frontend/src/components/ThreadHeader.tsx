interface ThreadHeaderProps {
  sessionLabel: string;
  workspaceRoot: string | null;
  workspaceExplicit: boolean;
  onPickWorkspace: () => void;
}

/** Left-hand content of the header row -- current session's label (its
 * own thread preview, same text NavRail's session list shows -- no
 * separate "coscribe" wordmark here since the OS window title already
 * says that) plus a workspace badge. The header's icon buttons (sidebar
 * toggle, settings) are rendered as siblings by App.tsx, sharing the
 * same row.
 *
 * The badge is the *only* way to pick a per-thread workspace now --
 * there used to also be a modal that force-opened on every new thread
 * (NewSessionWorkspacePicker); replaced with this opt-in click, matching
 * a live request: forcing the choice up front on a session that might
 * never need local files at all was the wrong default, not just
 * unpolished ("let me use it without connecting anything until I
 * actually want to"). Clickable only while workspaceExplicit is false --
 * a thread that already has an explicit choice rejects a second one
 * server-side (see web/session.py's select_workspace), so the badge
 * renders as plain, non-interactive text once that's happened rather
 * than inviting a click that can only ever fail. */
export function ThreadHeader({ sessionLabel, workspaceRoot, workspaceExplicit, onPickWorkspace }: ThreadHeaderProps) {
  // Basename only -- the full path is available as a tooltip, but the
  // badge itself has to stay short enough not to crowd out sessionLabel
  // in a header row that's already flex-1/min-w-0/truncate.
  const workspaceLabel = workspaceRoot?.split(/[\\/]/).filter(Boolean).pop();
  return (
    <div data-testid="thread-header" className="flex min-w-0 flex-1 items-center gap-2">
      {/* min-w-0 is load-bearing here, not decorative -- a flex item's
       * default min-width is `auto` (its content's intrinsic width), which
       * silently defeats `truncate`'s overflow-hidden/ellipsis for any
       * text long enough to need it: without this, a long first message
       * (thread preview is only capped at 200 chars server-side, not
       * cosmetically short) pushes the header row wider instead of
       * eliding, shoving the workspace badge and the settings gear off
       * to the right or wrapping the row entirely. */}
      <span className="min-w-0 truncate text-sm font-medium" title={sessionLabel}>
        {sessionLabel}
      </span>
      {workspaceLabel &&
        (workspaceExplicit ? (
          <span
            className="shrink-0 truncate rounded-full border border-[var(--border)] bg-[var(--card-bg)] px-2.5 py-0.5 text-xs"
            title={workspaceRoot ?? undefined}
          >
            {workspaceLabel}
          </span>
        ) : (
          <button
            type="button"
            className="shrink-0 truncate rounded-full border border-[var(--border)] bg-[var(--card-bg)] px-2.5 py-0.5 text-xs hover:border-[var(--accent)]"
            title={`${workspaceRoot} -- click to choose a different folder for this session`}
            onClick={onPickWorkspace}
          >
            {workspaceLabel}
          </button>
        ))}
    </div>
  );
}
