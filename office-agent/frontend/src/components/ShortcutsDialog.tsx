import { useEffect } from "react";
import { createPortal } from "react-dom";
import { CloseIcon } from "./icons";

interface Shortcut {
  keys: string[];
  description: string;
}

const GENERAL_SHORTCUTS: Shortcut[] = [
  { keys: ["?"], description: "Show this shortcuts list" },
  { keys: ["Esc"], description: "Close a dialog, or cancel editing a message" },
];

const COMPOSER_SHORTCUTS: Shortcut[] = [
  { keys: ["Enter"], description: "Send the message" },
  { keys: ["Shift", "Enter"], description: "Insert a new line" },
  { keys: ["Ctrl/Cmd", "Enter"], description: "Insert a new line" },
  { keys: ["/"], description: "Open the command list (type to filter, Enter to run)" },
];

const MODEL_PICKER_SHORTCUTS: Shortcut[] = [
  { keys: ["1", "-", "9"], description: "While the model picker is open, switch to that numbered model" },
];

interface ShortcutGroup {
  label: string;
  shortcuts: Shortcut[];
}

const GROUPS: ShortcutGroup[] = [
  { label: "General", shortcuts: GENERAL_SHORTCUTS },
  { label: "Composer", shortcuts: COMPOSER_SHORTCUTS },
  { label: "Model picker", shortcuts: MODEL_PICKER_SHORTCUTS },
];

interface ShortcutsDialogProps {
  onClose: () => void;
}

/** A discoverable list of the shortcuts already scattered across
 * Composer.tsx, ChatLog.tsx, and ModelPicker.tsx -- none of them were
 * announced anywhere in the UI before this, so a user had no way to find
 * out Ctrl/Cmd+Enter inserts a newline or that the model picker responds
 * to number keys short of reading the source. This dialog doesn't change
 * any of that existing key-handling code, it just documents it. */
export function ShortcutsDialog({ onClose }: ShortcutsDialogProps) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  // Portaled to document.body -- same fix as SettingsModal (see its
  // docstring) and ConfirmDialog.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="w-[min(420px,100vw-2rem)] rounded-[14px] border border-[var(--border)] bg-[var(--panel-bg)] p-4 shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <div className="text-sm font-medium">Keyboard shortcuts</div>
          <button
            type="button"
            aria-label="Close"
            className="flex h-6 w-6 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
            onClick={onClose}
          >
            <CloseIcon className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="flex flex-col gap-4">
          {GROUPS.map((group) => (
            <div key={group.label}>
              <div className="mb-1.5 text-xs font-medium tracking-wide text-[var(--muted)]">{group.label.toUpperCase()}</div>
              <div className="flex flex-col gap-1.5">
                {group.shortcuts.map((s, i) => (
                  <div key={i} className="flex items-center justify-between gap-3 text-sm">
                    <span className="text-[var(--muted)]">{s.description}</span>
                    <span className="flex shrink-0 items-center gap-1">
                      {s.keys.map((k, ki) => (
                        <kbd
                          key={ki}
                          className="rounded-md border border-[var(--border)] bg-[var(--card-bg)] px-1.5 py-0.5 font-mono text-xs"
                        >
                          {k}
                        </kbd>
                      ))}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}
