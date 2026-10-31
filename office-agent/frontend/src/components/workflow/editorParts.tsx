import type { ReactNode } from "react";
import { inputClass } from "../../lib/formStyles";
import { IDENTIFIER } from "../../lib/workflowEdit";
import type { ToolInfo } from "../../types/settings";
import type {
  AgentStep,
  LLMStep,
  OutputField,
  OutputFieldType,
  ScriptStep,
  ToolStep,
  WorkflowStep,
} from "../../types/workflow";
import { ToggleSwitch } from "../ToggleSwitch";
import { AddRowButton, FieldLabel, RemoveButton } from "./fields";
import { VarToken } from "./parts";

const FIELD_TYPE_LABEL: Record<OutputFieldType, string> = {
  text: "Text",
  number: "Number",
  boolean: "Yes / no",
  list: "List",
};

export interface EditorContext {
  /** Names this step may read, including model steps' fields. */
  suggestions: string[];
  inputNames: Set<string>;
  tools: Map<string, ToolInfo>;
  listId: string;
  /** For a loop: what its body makes, which it can keep from each pass. */
  collectable?: string[];
}

export interface EditorProps<T extends WorkflowStep> {
  step: T;
  onChange: (step: T) => void;
  context: EditorContext;
}

export function ValuesHint({ context }: { context: EditorContext }) {
  const roots = context.suggestions.filter((name) => !name.includes("."));
  if (roots.length === 0) return null;
  return (
    <div className="mt-1.5 flex flex-wrap items-baseline gap-1.5 text-xs text-[var(--muted)]">
      <span>Available:</span>
      {roots.map((name) => (
        <VarToken key={name} name={name} isInput={context.inputNames.has(name)} />
      ))}
      <span>
        -- write <span className="font-mono">{"{{name}}"}</span> to use one.
      </span>
    </div>
  );
}

export function SaveAsField<T extends ToolStep | ScriptStep | LLMStep | AgentStep>({
  step,
  onChange,
}: Omit<EditorProps<T>, "context">) {
  const value = step.save_as ?? "";
  const invalid = value !== "" && !IDENTIFIER.test(value);
  return (
    <div className="max-w-xs">
      <FieldLabel htmlFor={`${step.id}-save-as`}>Save result as</FieldLabel>
      <input
        id={`${step.id}-save-as`}
        spellCheck={false}
        className={`${inputClass} font-mono text-[12.5px]`}
        value={value}
        aria-invalid={invalid}
        placeholder={step.kind === "llm" ? "required" : "not saved"}
        onChange={(e) => onChange({ ...step, save_as: e.target.value.trim() || (step.kind === "llm" ? "" : null) })}
      />
      <p className={`mt-1 text-xs ${invalid ? "text-[var(--danger)]" : "text-[var(--muted)]"}`}>
        {invalid
          ? "Lowercase letters, digits and _, starting with a letter."
          : "Renaming it updates the later steps that read it."}
      </p>
    </div>
  );
}

/** The named fields a model or agent step must return. */
export function OutputFieldsTable({
  fields,
  minimum,
  hint,
  onChange,
  children,
}: {
  fields: OutputField[];
  minimum: number;
  hint: ReactNode;
  onChange: (fields: OutputField[]) => void;
  children: ReactNode;
}) {
  const setField = (index: number, changes: Partial<OutputField>) =>
    onChange(fields.map((f, i) => (i === index ? { ...f, ...changes } : f)));
  return (
    <div>
      <FieldLabel hint={hint}>{children}</FieldLabel>
      {fields.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-[var(--border)]">
          <div className="grid grid-cols-[minmax(0,160px)_120px_minmax(0,1fr)_32px] bg-[var(--card-bg)] text-xs text-[var(--muted)]">
            <div className="px-3 py-2">Field</div>
            <div className="px-3 py-2">Type</div>
            <div className="px-3 py-2">Meaning</div>
            <div />
          </div>
          {fields.map((field, index) => {
            const invalid = !IDENTIFIER.test(field.name);
            return (
              <div
                key={index}
                className="grid grid-cols-[minmax(0,160px)_120px_minmax(0,1fr)_32px] items-center border-t border-[var(--border)]"
              >
                <div className="px-1.5 py-1">
                  <input
                    aria-label={`Field ${index + 1} name`}
                    aria-invalid={invalid}
                    spellCheck={false}
                    className={`${inputClass} border-transparent py-1 font-mono text-[12.5px] ${invalid ? "text-[var(--danger)]" : ""}`}
                    value={field.name}
                    onChange={(e) => setField(index, { name: e.target.value.trim() })}
                  />
                </div>
                <div className="px-1.5 py-1">
                  <select
                    aria-label={`Type of field ${index + 1}`}
                    className={`${inputClass} border-transparent py-1`}
                    value={field.type}
                    onChange={(e) => setField(index, { type: e.target.value as OutputFieldType })}
                  >
                    {Object.entries(FIELD_TYPE_LABEL).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="px-1.5 py-1">
                  <input
                    aria-label={`Meaning of field ${index + 1}`}
                    className={`${inputClass} border-transparent py-1`}
                    value={field.description}
                    placeholder="What it should contain"
                    onChange={(e) => setField(index, { description: e.target.value })}
                  />
                </div>
                <div>
                  {fields.length > minimum && (
                    <RemoveButton
                      label={`Remove field ${field.name || index + 1}`}
                      onClick={() => onChange(fields.filter((_, i) => i !== index))}
                    />
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
      <div className="mt-1.5">
        <AddRowButton
          onClick={() => onChange([...fields, { name: `field${fields.length + 1}`, type: "text", description: "" }])}
        >
          Add a field
        </AddRowButton>
      </div>
    </div>
  );
}

export function IrreversibleToggle<T extends ToolStep | AgentStep>({
  step,
  onChange,
}: {
  step: T;
  onChange: (step: T) => void;
}) {
  return (
    <label className="flex items-start gap-2.5 text-sm">
      <ToggleSwitch
        on={step.irreversible}
        label="Can't be undone"
        onClick={() => onChange({ ...step, irreversible: !step.irreversible })}
      />
      <span>
        Can’t be undone
        <span className="block text-[13px] text-[var(--muted)]">
          It submits, sends, pays or deletes something. A test run stops before it, so checking the workflow doesn’t
          send anything.
        </span>
      </span>
    </label>
  );
}
