import { useRef, useState } from "react";
import { useClickOutside } from "../lib/useClickOutside";
import { CheckIcon } from "./icons";

type Mode = "manual" | "accept-edits" | "plan" | "auto";

// Claude Code's four permission modes, in its own order and wording.
const MODES: { mode: Mode; label: string; description: string }[] = [
  { mode: "manual", label: "Manual", description: "Asks before every change, script or outside action" },
  {
    mode: "accept-edits",
    label: "Accept Edits",
    description: "Edits files in your folders without asking; still asks before running code or outside actions",
  },
  { mode: "plan", label: "Plan", description: "Researches and writes a plan for you to approve before any change" },
  {
    mode: "auto",
    label: "Auto",
    description: "A reviewer model approves routine actions and blocks risky ones instead of asking you",
  },
];

interface ModePillProps {
  planMode: boolean;
  acceptEdits: boolean;
  autoMode: boolean;
  /** Sends a bare user_message with no chat-log bubble -- the backend's
   * "state" reply is the visible confirmation. */
  sendRaw: (text: string) => void;
}

function currentMode({ planMode, acceptEdits, autoMode }: Omit<ModePillProps, "sendRaw">): Mode {
  if (planMode) return "plan";
  if (acceptEdits) return "accept-edits";
  if (autoMode) return "auto";
  return "manual";
}

export function ModePill({ planMode, acceptEdits, autoMode, sendRaw }: ModePillProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useClickOutside(rootRef, () => setOpen(false), open);
  const current = currentMode({ planMode, acceptEdits, autoMode });

  // The server keeps one mode at a time: switching on a mode switches the
  // others off, and switching the current one off returns to Manual.
  const changeMode = (target: Mode) => {
    setOpen(false);
    if (target === current) return;
    sendRaw(target === "manual" ? `/${current}` : `/${target}`);
  };

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex h-8 items-center rounded-lg px-2 text-sm text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
        onClick={() => setOpen((v) => !v)}
      >
        {MODES.find((m) => m.mode === current)?.label}
      </button>
      {open && (
        <div
          role="menu"
          className="absolute bottom-full left-0 z-20 mb-1 w-80 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] p-1 shadow-[var(--shadow)]"
        >
          {MODES.map(({ mode, label, description }) => (
            <button
              key={mode}
              type="button"
              role="menuitemradio"
              aria-checked={mode === current}
              className="flex w-full items-start gap-2 rounded-md px-2.5 py-2 text-left hover:bg-[var(--card-bg)]"
              onClick={() => changeMode(mode)}
            >
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="text-sm">{label}</span>
                <span className="text-xs leading-snug text-[var(--muted)]">{description}</span>
              </span>
              {mode === current && <CheckIcon className="mt-0.5 h-4 w-4 shrink-0" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
