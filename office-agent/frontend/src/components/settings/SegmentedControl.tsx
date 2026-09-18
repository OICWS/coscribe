/** A small fixed set of mutually-exclusive choices as one bordered pill
 * group, the selected option in a raised white/bg chip -- explicit
 * request to replace free-text entry for a setting whose real values are
 * a short known list (Log Level: DEBUG/INFO/WARNING/ERROR), matching a
 * reference screenshot of this exact pattern. Reach for this whenever a
 * SettingRow's value is one of a handful of fixed strings; keep
 * SettingRowInput for anything genuinely open-ended (a path, a model id). */
export function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="flex items-center gap-0.5 rounded-lg border border-[var(--border)] bg-[var(--card-bg)] p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          className={`rounded-md px-2.5 py-1 text-sm transition-colors ${
            option.value === value
              ? "bg-[var(--bg)] text-[var(--fg)] shadow-sm"
              : "text-[var(--muted)] hover:text-[var(--fg)]"
          }`}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
