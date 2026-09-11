import type { ReactNode } from "react";

/** One setting: label + description on the left, a single control
 * (toggle, button, select, or a compact input) aligned right --
 * modeled on Claude Cowork's own Settings page, which reads as a flat
 * list of these with generous vertical rhythm and no per-row borders,
 * not the boxed/bordered card look used elsewhere in this app (that
 * treatment fits a *collection* of like items -- directories, MCP
 * servers -- where the box is doing real grouping work; a single
 * always-on-screen setting doesn't need one). Rendered by a parent
 * `<div className="flex flex-col divide-y divide-[var(--border)]">` --
 * the divider comes from that shared container, not repeated per row,
 * so adjacent rows don't double up a border between them. */
export function SettingRow({
  label,
  description,
  control,
}: {
  label: string;
  description?: ReactNode;
  control: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-8 py-4 first:pt-0 last:pb-0">
      <div className="flex min-w-0 flex-col gap-1">
        <span className="text-sm text-[var(--fg)]">{label}</span>
        {description && <span className="text-xs leading-relaxed text-[var(--muted)]">{description}</span>}
      </div>
      <div className="shrink-0">{control}</div>
    </div>
  );
}

/** A compact right-aligned text input for a SettingRow's control slot --
 * `WorkspaceTab`'s directory-path fields aside, most General-tab values
 * (a model id, a log level, a turn count) are short enough that a full-
 * width input under the label (the old layout) wasted the row's own
 * horizontal space for no reason. */
export function SettingRowInput({
  value,
  placeholder,
  onChange,
  monospace,
}: {
  value: string;
  placeholder?: string;
  onChange: (value: string) => void;
  monospace?: boolean;
}) {
  return (
    <input
      className={`w-56 rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none focus:border-[var(--accent)] ${monospace ? "font-mono text-xs" : ""}`}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
