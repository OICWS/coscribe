/** Two or more ways to look at one thing, as a row of pressed buttons. */
export function ViewToggle<View extends string>({
  label,
  views,
  value,
  onChange,
}: {
  label: string;
  views: { view: View; label: string }[];
  value: View;
  onChange: (view: View) => void;
}) {
  return (
    <div role="group" aria-label={label} className="flex gap-1 self-start rounded-lg bg-[var(--card-bg)] p-0.5">
      {views.map((item) => (
        <button
          key={item.view}
          type="button"
          aria-pressed={value === item.view}
          className={`h-7 rounded-md px-3 text-[13px] ${
            value === item.view ? "bg-[var(--bg)] font-medium shadow-sm" : "text-[var(--muted)] hover:text-[var(--fg)]"
          }`}
          onClick={() => onChange(item.view)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
