import { useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import type { WorkflowInput } from "../../types/workflow";
import { PlayIcon } from "../icons";
import { VarToken } from "./parts";

function placeholderFor(input: WorkflowInput): string | undefined {
  if (input.required === false) return "Can be left blank";
  if (input.type === "file") return "A path in the workspace, e.g. reports/march.pdf";
  return undefined;
}

interface RunInputsDialogProps {
  taskName: string;
  inputs: WorkflowInput[];
  onCancel: () => void;
  onRun: (values: Record<string, unknown>) => void;
}

/** Run now for a workflow with inputs: the defaults, confirmed or changed
 * for this one run. */
export function RunInputsDialog({ taskName, inputs, onCancel, onRun }: RunInputsDialogProps) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(inputs.map((input) => [input.name, input.default === null ? "" : String(input.default)])),
  );
  const missing = inputs.filter((input) => input.required !== false && !values[input.name]?.trim());

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (missing.length > 0) return;
    onRun(
      Object.fromEntries(
        inputs.map((input) => {
          const value = values[input.name].trim();
          return [input.name, input.type === "number" && value !== "" ? Number(value) : value];
        }),
      ),
    );
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onCancel}>
      <form
        className="w-[min(460px,100vw-2rem)] rounded-[16px] border border-[var(--border)] bg-[var(--panel-bg)] p-5 shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
      >
        <h2 className="text-base font-semibold">Run {taskName}</h2>
        <p className="mt-0.5 text-sm text-[var(--muted)]">These values are used for this run only.</p>
        <div className="mt-4 flex flex-col gap-3.5">
          {inputs.map((input, index) => (
            <label key={input.name} className="flex flex-col gap-1.5">
              {/* A long label pushes the name onto its own line instead of truncating it. */}
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                <span>{input.label || input.name}</span>
                <span className="shrink-0">
                  <VarToken name={input.name} isInput />
                </span>
                {input.required === false && <span className="text-xs text-[var(--muted)]">Optional</span>}
              </span>
              <input
                autoFocus={index === 0}
                type={input.type === "number" ? "number" : "text"}
                value={values[input.name] ?? ""}
                placeholder={placeholderFor(input)}
                onChange={(e) => setValues((prev) => ({ ...prev, [input.name]: e.target.value }))}
                className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2.5 py-1.5 text-sm outline-none hover:border-[var(--border-hover)] focus:border-[var(--border-hover)]"
              />
            </label>
          ))}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            className="h-8 rounded-md border border-[var(--border)] px-3 text-sm hover:bg-[var(--card-bg)]"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={missing.length > 0}
            className="flex h-8 items-center gap-1.5 rounded-md bg-[var(--primary)] px-3 text-sm font-medium text-[var(--primary-fg)] hover:bg-[var(--primary-hover)] disabled:opacity-50"
          >
            <PlayIcon className="h-3.5 w-3.5" /> Run
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
