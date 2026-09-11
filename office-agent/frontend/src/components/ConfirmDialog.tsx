import type { ReactNode } from "react";

interface ConfirmDialogProps {
  title: string;
  description: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  onCancel: () => void;
  onConfirm: () => void;
}

/** Shared styled confirm dialog -- matches SettingsModal's overlay/card
 * convention. Replaces the mix of window.confirm() (unstyled, looks like
 * the browser interrupted the app) and, in one case, no confirmation at
 * all that had accumulated across Thread/Workflow/Workflow-run/Provider
 * delete before this was unified. */
export function ConfirmDialog({
  title,
  description,
  confirmLabel = "Delete",
  cancelLabel = "Cancel",
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onCancel}>
      <div
        className="w-[min(360px,100vw-2rem)] rounded-[14px] border border-[var(--border)] bg-[var(--panel-bg)] p-4 shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-1 text-sm font-medium">{title}</div>
        <div className="mb-4 text-sm text-[var(--muted)]">{description}</div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            className="rounded-md border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--card-bg)]"
            onClick={onCancel}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className="rounded-md bg-red-600 px-3 py-1.5 text-sm text-white hover:bg-red-700"
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
