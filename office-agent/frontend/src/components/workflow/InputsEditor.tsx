import { useState } from "react";
import { controlClass, primaryButton, secondaryButton } from "../../lib/formStyles";
import { IDENTIFIER } from "../../lib/workflowEdit";
import type { WorkflowInput } from "../../types/workflow";
import { AddRowButton, RemoveButton } from "./fields";
import { SectionHeading, VarToken } from "./parts";

const INPUT_TYPE_LABEL: Record<WorkflowInput["type"], string> = { text: "Text", file: "File", number: "Number" };

interface Row extends WorkflowInput {
  /** The name this input had when editing began, to carry references over. */
  original: string | null;
}

function ReadOnlyInputs({ inputs }: { inputs: WorkflowInput[] }) {
  if (inputs.length === 0) {
    return (
      <p className="text-[13px] text-[var(--muted)]">
        None -- every run uses the same values. Add one for anything that changes run to run, like a file.
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-2">
      {inputs.map((input) => (
        <li
          key={input.name}
          className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-[var(--border)] px-3.5 py-2.5"
        >
          <VarToken name={input.name} isInput />
          <span className="text-[13px] text-[var(--muted)]">
            {input.label || INPUT_TYPE_LABEL[input.type]} · asked at each run
          </span>
          <span className="flex-1" />
          {input.default !== null && input.default !== "" && (
            <span className="flex items-center gap-2 text-[13px]">
              <span className="text-[var(--muted)]">Default</span>
              <span className="rounded-md bg-[var(--card-bg)] px-2 py-0.5">{String(input.default)}</span>
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

function blankRow(index: number): Row {
  return { name: `input${index + 1}`, label: "", type: "text", default: null, original: null };
}

interface InputsEditorProps {
  inputs: WorkflowInput[];
  /** Resolves to an error message, or null once saved. */
  onSave: (inputs: WorkflowInput[], renames: [string, string][]) => Promise<string | null>;
}

export function InputsEditor({ inputs, onSave }: InputsEditorProps) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const startEditing = () => {
    const current: Row[] = inputs.map((input) => ({ ...input, original: input.name }));
    setRows(current.length > 0 ? current : [blankRow(0)]);
    setError(null);
  };
  const update = (index: number, changes: Partial<Row>) =>
    setRows((current) => current?.map((row, i) => (i === index ? { ...row, ...changes } : row)) ?? null);

  const save = async () => {
    if (!rows) return;
    setSaving(true);
    const renames = rows
      .filter((row) => row.original !== null && row.original !== row.name)
      .map((row) => [row.original as string, row.name] as [string, string]);
    const cleaned: WorkflowInput[] = rows.map(({ original: _original, ...input }) => ({
      ...input,
      default: input.default === "" ? null : input.default,
    }));
    const problem = await onSave(cleaned, renames);
    setSaving(false);
    setError(problem);
    if (!problem) setRows(null);
  };

  const invalidName = rows?.some((row) => !IDENTIFIER.test(row.name)) ?? false;

  return (
    <section>
      <SectionHeading
        aside={
          rows === null && (
            <button
              type="button"
              className="rounded-md px-2 py-0.5 text-xs text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
              onClick={startEditing}
            >
              {inputs.length === 0 ? "Add an input" : "Edit"}
            </button>
          )
        }
      >
        Inputs
      </SectionHeading>
      {rows === null ? (
        <ReadOnlyInputs inputs={inputs} />
      ) : (
        <div className="flex flex-col gap-2 rounded-xl border border-[color-mix(in_srgb,var(--accent)_70%,transparent)] p-3">
          <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_100px_minmax(0,1fr)_28px] gap-2 px-1 text-xs text-[var(--muted)]">
            <span>Name</span>
            <span>Shown as</span>
            <span>Type</span>
            <span>Default</span>
            <span />
          </div>
          {rows.map((row, index) => (
            <div
              key={index}
              className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_100px_minmax(0,1fr)_28px] items-center gap-2"
            >
              <input
                aria-label={`Input ${index + 1} name`}
                aria-invalid={!IDENTIFIER.test(row.name)}
                spellCheck={false}
                className={`${controlClass} min-w-0 font-mono text-[12.5px]`}
                value={row.name}
                onChange={(e) => update(index, { name: e.target.value.trim() })}
              />
              <input
                aria-label={`Input ${index + 1} label`}
                className={`${controlClass} min-w-0`}
                placeholder="e.g. PDF to audit"
                value={row.label}
                onChange={(e) => update(index, { label: e.target.value })}
              />
              <select
                aria-label={`Input ${index + 1} type`}
                className={`${controlClass} min-w-0`}
                value={row.type}
                onChange={(e) => update(index, { type: e.target.value as WorkflowInput["type"] })}
              >
                {Object.entries(INPUT_TYPE_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <input
                aria-label={`Input ${index + 1} default`}
                className={`${controlClass} min-w-0`}
                type={row.type === "number" ? "number" : "text"}
                value={row.default === null ? "" : String(row.default)}
                placeholder="Asked each run"
                onChange={(e) =>
                  update(index, {
                    default: row.type === "number" && e.target.value !== "" ? Number(e.target.value) : e.target.value,
                  })
                }
              />
              <RemoveButton
                label={`Remove input ${row.name || index + 1}`}
                onClick={() => setRows(rows.filter((_, i) => i !== index))}
              />
            </div>
          ))}
          <AddRowButton onClick={() => setRows([...rows, blankRow(rows.length)])}>Add an input</AddRowButton>
          {invalidName && (
            <p className="text-xs text-[var(--danger)]">
              Names use lowercase letters, digits and _, starting with a letter.
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm text-[var(--danger)]">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" className={secondaryButton} onClick={() => setRows(null)}>
              Cancel
            </button>
            <button type="button" disabled={saving || invalidName} className={primaryButton} onClick={save}>
              {saving ? "Saving…" : "Save inputs"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
