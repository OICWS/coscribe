import { useEffect, useRef, type ReactNode } from "react";
import { inputClass } from "../../lib/formStyles";
import { CloseIcon } from "../icons";

function useAutoHeight(value: string) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [value]);
  return ref;
}

export function AutoTextarea({
  id,
  value,
  onChange,
  mono,
  placeholder,
  label,
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  mono?: boolean;
  placeholder?: string;
  label?: string;
}) {
  const ref = useAutoHeight(value);
  return (
    <textarea
      id={id}
      ref={ref}
      rows={mono ? 4 : 3}
      value={value}
      aria-label={label}
      placeholder={placeholder}
      spellCheck={!mono}
      onChange={(e) => onChange(e.target.value)}
      className={`${inputClass} resize-none leading-relaxed ${mono ? "font-mono text-[12.5px]" : ""}`}
    />
  );
}

export function FieldLabel({ htmlFor, children, hint }: { htmlFor?: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-1.5 flex items-baseline justify-between gap-3">
      <label htmlFor={htmlFor} className="text-xs font-medium text-[var(--muted)]">
        {children}
      </label>
      {hint && <span className="text-right text-xs text-[var(--muted)]">{hint}</span>}
    </div>
  );
}

export function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--danger)]"
      onClick={onClick}
    >
      <CloseIcon className="h-3.5 w-3.5" />
    </button>
  );
}

export function AddRowButton({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      className="self-start rounded-md px-2 py-1 text-[13px] text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
      onClick={onClick}
    >
      + {children}
    </button>
  );
}
