import { useRef, useState } from "react";
import { useClickOutside } from "../lib/useClickOutside";
import { ChevronDownIcon } from "./icons";

type Mode = "normal" | "plan" | "accept-edits";

const MODE_LABELS: Record<Mode, string> = {
  normal: "Normal",
  plan: "Plan",
  "accept-edits": "Accept Edits",
};

interface ModePillProps {
  planMode: boolean;
  acceptEdits: boolean;
  /** Sends a bare user_message with no chat-log bubble (mirrors app.js's
   * sendRaw) -- the backend's "state" reply is the visible confirmation. */
  sendRaw: (text: string) => void;
}

export function ModePill({ planMode, acceptEdits, sendRaw }: ModePillProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useClickOutside(rootRef, () => setOpen(false), open);
  const current: Mode = planMode ? "plan" : acceptEdits ? "accept-edits" : "normal";

  const changeMode = (target: Mode) => {
    setOpen(false);
    if (target === current) return;
    if (current !== "normal") sendRaw(`/${current}`);
    if (target !== "normal") sendRaw(`/${target}`);
  };

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        className="flex h-8 items-center gap-1 rounded-lg px-2 text-sm text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
        onClick={() => setOpen((v) => !v)}
      >
        {MODE_LABELS[current]}
        <ChevronDownIcon className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div className="absolute bottom-full left-0 mb-1 min-w-32 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] shadow-[var(--shadow)]">
          {(Object.keys(MODE_LABELS) as Mode[]).map((mode) => (
            <div
              key={mode}
              className="cursor-pointer px-3 py-2 text-sm hover:bg-[var(--card-bg)]"
              onClick={() => changeMode(mode)}
            >
              {mode === current ? "✓ " : ""}
              {MODE_LABELS[mode]}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
