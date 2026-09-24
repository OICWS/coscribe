import { useState } from "react";
import { controlClass, inputClass } from "../../lib/formStyles";
import { toolLabel } from "../../lib/toolLabels";
import { IDENTIFIER } from "../../lib/workflowEdit";
import type { ToolInfo, ToolParam } from "../../types/settings";
import type {
  ApprovalStep,
  BranchStep,
  CheckStep,
  LLMStep,
  LoopStep,
  OutputFieldType,
  ScriptStep,
  ToolStep,
  WorkflowStep,
} from "../../types/workflow";
import { ToggleSwitch } from "../ToggleSwitch";
import { ConditionRow } from "./ConditionRow";
import { AddRowButton, AutoTextarea, FieldLabel, RemoveButton } from "./fields";
import { VarToken } from "./parts";

const FIELD_TYPE_LABEL: Record<OutputFieldType, string> = {
  text: "Text",
  number: "Number",
  boolean: "Yes / no",
  list: "List",
};

// Arguments whose values are usually paragraphs, not a word or a path.
const LONG_ARGS = new Set(["content", "script", "data", "text", "prompt", "body", "notes", "message", "code"]);

export interface EditorContext {
  /** Names this step may read, including model steps' fields. */
  suggestions: string[];
  inputNames: Set<string>;
  tools: Map<string, ToolInfo>;
  listId: string;
  /** For a loop: what its body makes, which it can keep from each pass. */
  collectable?: string[];
}

interface EditorProps<T extends WorkflowStep> {
  step: T;
  onChange: (step: T) => void;
  context: EditorContext;
}

function ValuesHint({ context }: { context: EditorContext }) {
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

function SaveAsField<T extends ToolStep | ScriptStep | LLMStep>({ step, onChange }: Omit<EditorProps<T>, "context">) {
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

// -- Tool ------------------------------------------------------------------------

function parseNumberish(raw: string): unknown {
  return /^-?\d+(\.\d+)?$/.test(raw.trim()) ? Number(raw) : raw;
}

function JsonArg({ id, value, onChange }: { id: string; value: unknown; onChange: (value: unknown) => void }) {
  const [text, setText] = useState(() => (value === undefined ? "" : JSON.stringify(value, null, 2)));
  const [invalid, setInvalid] = useState(false);
  return (
    <div>
      <AutoTextarea
        id={id}
        mono
        value={text}
        placeholder="JSON, e.g. [1, 2]"
        onChange={(next) => {
          setText(next);
          if (next.trim() === "") {
            setInvalid(false);
            onChange(undefined);
            return;
          }
          try {
            onChange(JSON.parse(next));
            setInvalid(false);
          } catch {
            setInvalid(true);
          }
        }}
      />
      {invalid && (
        <p className="mt-1 text-xs text-[var(--danger)]">Not valid JSON yet -- the last valid value is kept.</p>
      )}
    </div>
  );
}

function ArgControl({
  id,
  param,
  value,
  onChange,
}: {
  id: string;
  param: ToolParam;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  if (param.type === "boolean" && typeof value !== "string") {
    let current = "default";
    if (value !== undefined) current = value ? "yes" : "no";
    return (
      <select
        id={id}
        className={inputClass}
        value={current}
        onChange={(e) => onChange(e.target.value === "default" ? undefined : e.target.value === "yes")}
      >
        {!param.required && <option value="default">Default ({param.default ? "yes" : "no"})</option>}
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    );
  }
  if (param.type === "other" && typeof value !== "string") {
    return <JsonArg id={id} value={value} onChange={onChange} />;
  }
  const text = value === undefined ? "" : String(value);
  const placeholder = param.default !== null && param.default !== "" ? `Default: ${String(param.default)}` : undefined;
  const commit = (raw: string) => {
    if (raw === "" && !param.required) onChange(undefined);
    else onChange(param.type === "number" ? parseNumberish(raw) : raw);
  };
  if (LONG_ARGS.has(param.name) || text.includes("\n")) {
    return <AutoTextarea id={id} value={text} placeholder={placeholder} onChange={commit} />;
  }
  return (
    <input
      id={id}
      className={inputClass}
      value={text}
      placeholder={placeholder}
      inputMode={param.type === "number" ? "decimal" : undefined}
      onChange={(e) => commit(e.target.value)}
    />
  );
}

function ToolEditor({ step, onChange, context }: EditorProps<ToolStep>) {
  const info = context.tools.get(step.tool);
  const known = new Set(info?.params.map((p) => p.name) ?? []);
  const params: ToolParam[] = [
    ...(info?.params ?? []),
    ...Object.keys(step.args)
      .filter((name) => !known.has(name))
      .map((name) => ({ name, type: "text" as const, required: false, default: null, description: "" })),
  ];
  const setArg = (name: string, value: unknown) => {
    const args = { ...step.args };
    if (value === undefined) delete args[name];
    else args[name] = value;
    onChange({ ...step, args });
  };
  return (
    <>
      <div>
        <FieldLabel>
          {toolLabel(step.tool)}
          {info?.description && <span className="font-normal"> · {info.description}</span>}
        </FieldLabel>
        {!info && (
          <p className="mb-2 text-xs text-[var(--danger)]">
            There's no tool called {step.tool} here -- the run will refuse this step.
          </p>
        )}
        <div className="grid grid-cols-[minmax(0,150px)_minmax(0,1fr)] items-start gap-x-3 gap-y-2.5">
          {params.map((param) => (
            <div key={param.name} className="contents">
              <label
                htmlFor={`${step.id}-arg-${param.name}`}
                className="pt-1.5 font-mono text-[12.5px] text-[var(--muted)]"
              >
                {param.name}
                {param.required && <span className="text-[var(--danger)]"> *</span>}
              </label>
              <div>
                <ArgControl
                  id={`${step.id}-arg-${param.name}`}
                  param={param}
                  value={step.args[param.name]}
                  onChange={(value) => setArg(param.name, value)}
                />
                {param.description && <p className="mt-1 text-xs text-[var(--muted)]">{param.description}</p>}
              </div>
            </div>
          ))}
        </div>
        <ValuesHint context={context} />
      </div>
      <SaveAsField step={step} onChange={onChange} />
    </>
  );
}

// -- Script ---------------------------------------------------------------------

function ScriptEditor({ step, onChange, context }: EditorProps<ScriptStep>) {
  const entries = Object.entries(step.inputs);
  const setEntries = (next: [string, unknown][]) => onChange({ ...step, inputs: Object.fromEntries(next) });
  return (
    <>
      <div>
        <FieldLabel htmlFor={`${step.id}-code`} hint="Prints its result as JSON on the last line">
          Python
        </FieldLabel>
        <AutoTextarea id={`${step.id}-code`} value={step.code} mono onChange={(code) => onChange({ ...step, code })} />
      </div>
      <div className="flex flex-col gap-2">
        <FieldLabel hint={'The script reads each as inputs["name"]'}>Reads</FieldLabel>
        {entries.map(([key, value], index) => (
          <div key={index} className="flex items-center gap-2">
            <input
              aria-label={`Input ${index + 1} name`}
              spellCheck={false}
              className={`${controlClass} w-36 font-mono text-[12.5px]`}
              value={key}
              onChange={(e) =>
                setEntries(entries.map((entry, i) => (i === index ? [e.target.value.trim(), entry[1]] : entry)))
              }
            />
            <span className="text-[var(--muted)]">=</span>
            <input
              aria-label={`Input ${index + 1} value`}
              className={`${controlClass} min-w-0 flex-1`}
              value={typeof value === "string" ? value : JSON.stringify(value)}
              placeholder="{{name}} or text"
              onChange={(e) =>
                setEntries(entries.map((entry, i) => (i === index ? [entry[0], e.target.value] : entry)))
              }
            />
            <RemoveButton
              label={`Remove input ${key || index + 1}`}
              onClick={() => setEntries(entries.filter((_, i) => i !== index))}
            />
          </div>
        ))}
        <AddRowButton onClick={() => setEntries([...entries, [`value${entries.length + 1}`, ""]])}>
          Add a value
        </AddRowButton>
        <ValuesHint context={context} />
      </div>
      <SaveAsField step={step} onChange={onChange} />
    </>
  );
}

// -- Model -------------------------------------------------------------------------

function LLMEditor({ step, onChange, context }: EditorProps<LLMStep>) {
  const setField = (index: number, changes: Partial<LLMStep["fields"][number]>) =>
    onChange({ ...step, fields: step.fields.map((f, i) => (i === index ? { ...f, ...changes } : f)) });
  return (
    <>
      <div>
        <FieldLabel htmlFor={`${step.id}-prompt`}>Instructions</FieldLabel>
        <AutoTextarea
          id={`${step.id}-prompt`}
          value={step.prompt}
          placeholder="What the model should work out, from which values"
          onChange={(prompt) => onChange({ ...step, prompt })}
        />
        <ValuesHint context={context} />
      </div>
      <div>
        <FieldLabel hint="Anything else is sent back once, then the step fails">Must return</FieldLabel>
        <div className="overflow-hidden rounded-lg border border-[var(--border)]">
          <div className="grid grid-cols-[minmax(0,160px)_120px_minmax(0,1fr)_32px] bg-[var(--card-bg)] text-xs text-[var(--muted)]">
            <div className="px-3 py-2">Field</div>
            <div className="px-3 py-2">Type</div>
            <div className="px-3 py-2">Meaning</div>
            <div />
          </div>
          {step.fields.map((field, index) => {
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
                  {step.fields.length > 1 && (
                    <RemoveButton
                      label={`Remove field ${field.name || index + 1}`}
                      onClick={() => onChange({ ...step, fields: step.fields.filter((_, i) => i !== index) })}
                    />
                  )}
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-1.5">
          <AddRowButton
            onClick={() =>
              onChange({
                ...step,
                fields: [...step.fields, { name: `field${step.fields.length + 1}`, type: "text", description: "" }],
              })
            }
          >
            Add a field
          </AddRowButton>
        </div>
      </div>
      <div className="flex flex-wrap gap-4">
        <div className="w-72">
          <FieldLabel htmlFor={`${step.id}-model`}>Model</FieldLabel>
          <input
            id={`${step.id}-model`}
            className={inputClass}
            value={step.model ?? ""}
            placeholder="The task's model"
            onChange={(e) => onChange({ ...step, model: e.target.value.trim() || null })}
          />
        </div>
        <SaveAsField step={step} onChange={onChange} />
      </div>
    </>
  );
}

// -- Check / approval ----------------------------------------------------------------

function CheckEditor({ step, onChange, context }: EditorProps<CheckStep>) {
  const defaultReference = context.suggestions[0] ?? "";
  return (
    <div className="flex flex-col gap-2">
      <FieldLabel hint="All must hold, or the run stops here">Checks</FieldLabel>
      {step.conditions.map((condition, index) => (
        <ConditionRow
          key={index}
          label={`Check ${index + 1}`}
          condition={condition}
          valuesListId={context.listId}
          defaultReference={defaultReference}
          onChange={(next) =>
            onChange({ ...step, conditions: step.conditions.map((c, i) => (i === index ? next : c)) })
          }
          onRemove={
            step.conditions.length > 1
              ? () => onChange({ ...step, conditions: step.conditions.filter((_, i) => i !== index) })
              : undefined
          }
        />
      ))}
      <AddRowButton
        onClick={() =>
          onChange({
            ...step,
            conditions: [...step.conditions, { left: { ref: defaultReference }, op: "eq", right: { value: "" } }],
          })
        }
      >
        Add a check
      </AddRowButton>
    </div>
  );
}

function ApprovalEditor({ step, onChange, context }: EditorProps<ApprovalStep>) {
  const defaultReference = context.suggestions[0] ?? "";
  return (
    <>
      <div>
        <FieldLabel htmlFor={`${step.id}-message`}>What to show you</FieldLabel>
        <AutoTextarea
          id={`${step.id}-message`}
          value={step.message}
          onChange={(message) => onChange({ ...step, message })}
        />
        <ValuesHint context={context} />
      </div>
      <div className="flex flex-col gap-2">
        <label className="flex items-center gap-2.5 text-sm">
          <ToggleSwitch
            on={step.when !== null}
            label="Only pause when a condition holds"
            onClick={() =>
              onChange({
                ...step,
                when: step.when ? null : { left: { ref: defaultReference }, op: "eq", right: { value: true } },
              })
            }
          />
          Only pause when…
        </label>
        {step.when && (
          <ConditionRow
            label="Condition"
            condition={step.when}
            valuesListId={context.listId}
            defaultReference={defaultReference}
            onChange={(when) => onChange({ ...step, when })}
          />
        )}
      </div>
    </>
  );
}

// -- Branch / loop -------------------------------------------------------------------

function BranchEditor({ step, onChange, context }: EditorProps<BranchStep>) {
  return (
    <div className="flex flex-col gap-2">
      <FieldLabel hint="Its Then steps run when this holds; the Otherwise steps when it doesn't">Condition</FieldLabel>
      <ConditionRow
        label="Condition"
        condition={step.condition}
        valuesListId={context.listId}
        defaultReference={context.suggestions[0] ?? ""}
        onChange={(condition) => onChange({ ...step, condition })}
      />
    </div>
  );
}

function LoopEditor({ step, onChange, context }: EditorProps<LoopStep>) {
  const collectable = context.collectable ?? [];
  const itemInvalid = !IDENTIFIER.test(step.item);
  const saveAsInvalid = step.save_as !== null && !IDENTIFIER.test(step.save_as);
  const setCollect = (collect: string) => {
    if (!collect) {
      onChange({ ...step, collect: null, save_as: null });
      return;
    }
    const root = collect.split(".")[0];
    onChange({ ...step, collect, save_as: step.save_as ?? `${root}_list` });
  };
  return (
    <>
      <div className="grid grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_110px] gap-3">
        <div>
          <FieldLabel htmlFor={`${step.id}-over`}>Go through</FieldLabel>
          <input
            id={`${step.id}-over`}
            list={context.listId}
            spellCheck={false}
            aria-invalid={!step.over}
            className={`${inputClass} font-mono text-[12.5px]`}
            placeholder="a list, e.g. matches"
            value={step.over}
            onChange={(e) => onChange({ ...step, over: e.target.value.trim() })}
          />
        </div>
        <div>
          <FieldLabel htmlFor={`${step.id}-item`}>Call each one</FieldLabel>
          <input
            id={`${step.id}-item`}
            spellCheck={false}
            aria-invalid={itemInvalid}
            className={`${inputClass} font-mono text-[12.5px]`}
            value={step.item}
            onChange={(e) => onChange({ ...step, item: e.target.value.trim() })}
          />
        </div>
        <div>
          <FieldLabel htmlFor={`${step.id}-max`}>At most</FieldLabel>
          <input
            id={`${step.id}-max`}
            type="number"
            min={1}
            max={200}
            className={inputClass}
            value={step.max_items}
            onChange={(e) => onChange({ ...step, max_items: Math.max(1, Math.min(200, Number(e.target.value) || 1)) })}
          />
        </div>
      </div>
      <p className="-mt-2 text-xs text-[var(--muted)]">
        The steps inside run once per item, in order, reading it as{" "}
        <span className="font-mono">{`{{${step.item || "item"}}}`}</span>. A longer list stops the run rather than being
        cut short.
      </p>
      <div className="grid grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_110px] gap-3">
        <div>
          <FieldLabel htmlFor={`${step.id}-collect`}>Keep from each pass</FieldLabel>
          <select
            id={`${step.id}-collect`}
            className={inputClass}
            value={step.collect ?? ""}
            onChange={(e) => setCollect(e.target.value)}
          >
            <option value="">Nothing</option>
            {step.collect && !collectable.includes(step.collect) && (
              <option value={step.collect}>{step.collect}</option>
            )}
            {collectable.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>
        {step.collect !== null && (
          <div>
            <FieldLabel htmlFor={`${step.id}-save-as`}>Save the list as</FieldLabel>
            <input
              id={`${step.id}-save-as`}
              spellCheck={false}
              aria-invalid={saveAsInvalid}
              className={`${inputClass} font-mono text-[12.5px]`}
              value={step.save_as ?? ""}
              onChange={(e) => onChange({ ...step, save_as: e.target.value.trim() })}
            />
          </div>
        )}
      </div>
      {(itemInvalid || saveAsInvalid) && (
        <p className="-mt-2 text-xs text-[var(--danger)]">
          Names use lowercase letters, digits and _, starting with a letter.
        </p>
      )}
    </>
  );
}

/** The kind-specific part of an open step. */
export function StepFields({ step, onChange, context }: EditorProps<WorkflowStep>) {
  const body = (() => {
    switch (step.kind) {
      case "tool":
        return <ToolEditor step={step} onChange={onChange} context={context} />;
      case "script":
        return <ScriptEditor step={step} onChange={onChange} context={context} />;
      case "llm":
        return <LLMEditor step={step} onChange={onChange} context={context} />;
      case "check":
        return <CheckEditor step={step} onChange={onChange} context={context} />;
      case "approval":
        return <ApprovalEditor step={step} onChange={onChange} context={context} />;
      case "branch":
        return <BranchEditor step={step} onChange={onChange} context={context} />;
      case "loop":
        return <LoopEditor step={step} onChange={onChange} context={context} />;
    }
  })();
  return (
    <>
      <datalist id={context.listId}>
        {context.suggestions.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>
      {body}
    </>
  );
}
