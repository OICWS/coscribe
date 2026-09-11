import { useEffect, useRef, useState } from "react";
import { useClickOutside } from "../lib/useClickOutside";
import { ChevronDownIcon } from "./icons";

interface ProviderInfo {
  base_url: string | null;
  default_model: string;
  masked_key: string | null;
  builtin: boolean;
}

interface ModelPickerProps {
  currentModel: string;
  onSwitch: (model: string) => void;
}

export function ModelPicker({ currentModel, onSwitch }: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<[string, ProviderInfo][]>([]);
  const rootRef = useRef<HTMLDivElement>(null);
  useClickOutside(rootRef, () => setOpen(false), open);

  useEffect(() => {
    if (!open) return;
    // Re-fetched on every pill open, not cached -- a provider added
    // moments ago in Settings should show up here without a page reload.
    fetch("/api/providers")
      .then((res) => res.json())
      .then((providers: Record<string, ProviderInfo>) => {
        setEntries(Object.entries(providers).filter(([, info]) => info.default_model));
      });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      const tag = (event.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (event.key < "1" || event.key > "9") return;
      const entry = entries[Number(event.key) - 1];
      if (!entry) return;
      event.preventDefault();
      const [providerKey, info] = entry;
      const value = `${providerKey}:${info.default_model}`;
      setOpen(false);
      if (value !== currentModel) onSwitch(value);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, entries, currentModel, onSwitch]);

  const shortLabel = currentModel.includes(":") ? currentModel.split(":", 2)[1] : currentModel || "Select model";

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        data-testid="model-pill"
        className="flex h-8 max-w-[9rem] items-center gap-1 rounded-lg px-2 text-sm text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="truncate">{shortLabel}</span>
        <ChevronDownIcon className="h-3.5 w-3.5 shrink-0" />
      </button>
      {open && (
        <div className="absolute bottom-full left-0 mb-1 min-w-48 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] shadow-[var(--shadow)]">
          {entries.map(([providerKey, info], index) => {
            const value = `${providerKey}:${info.default_model}`;
            const isActive = value === currentModel;
            return (
              <div
                key={providerKey}
                className="flex items-center justify-between gap-3 px-3 py-2 text-sm hover:bg-[var(--card-bg)]"
                title={value}
                onClick={() => {
                  setOpen(false);
                  if (value !== currentModel) onSwitch(value);
                }}
              >
                <span className="cursor-pointer">
                  {isActive ? "✓ " : ""}
                  {info.default_model}
                </span>
                {index < 9 && <span className="text-xs text-[var(--muted)]">{index + 1}</span>}
              </div>
            );
          })}
          {entries.length === 0 && <div className="px-3 py-2 text-sm text-[var(--muted)]">No providers configured</div>}
        </div>
      )}
    </div>
  );
}
