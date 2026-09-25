interface ThreadHeaderProps {
  sessionLabel: string;
}

/** Left-hand content of the header row: the current session's label. The
 * header's icon buttons are rendered as siblings by App.tsx. */
export function ThreadHeader({ sessionLabel }: ThreadHeaderProps) {
  return (
    <div data-testid="thread-header" className="flex min-w-0 flex-1 items-center gap-2">
      {/* min-w-0: a flex item's default min-width is its content's width,
       * which defeats truncate for a long label. */}
      <span className="min-w-0 truncate text-sm font-medium" title={sessionLabel}>
        {sessionLabel}
      </span>
    </div>
  );
}
