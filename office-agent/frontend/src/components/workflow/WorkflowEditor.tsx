import { useEffect, useRef, useState, type ReactNode } from "react";
import { updateScheduledTask } from "../../lib/rest";
import { taskPayload } from "../../lib/taskPayload";
import { toolLabel } from "../../lib/toolLabels";
import { formatValue, namesBefore, OP_LABEL, stepOutput, wholeReference } from "../../lib/workflowLabels";
import type { ScheduledTask } from "../../types/settings";
import type {
  CheckOp,
  Condition,
  LLMStep,
  OutputFieldType,
  Workflow,
  WorkflowInput,
  WorkflowStep,
} from "../../types/workflow";
import { ChevronDownIcon } from "../icons";
import { ConditionText, KindLegend, OperandText, SectionHeading, StepKindTile, VarToken } from "./parts";

const INPUT_TYPE_LABEL: Record<WorkflowInput["type"], string> = { text: "Text", file: "File", number: "Number" };
const FIELD_TYPE_LABEL: Record<OutputFieldType, string> = {
  text: "Text",
  number: "Number",
  boolean: "Yes / no",
  list: "List",
};
const ARG_PREVIEW_CHARS = 32;

const controlClass =
  "rounded-md border border-[var(--border)] bg-[var(--bg)] px-2.5 py-1.5 text-sm outline-none transition-colors placeholder:text-[var(--muted)] hover:border-[var(--border-hover)] focus:border-[var(--border-hover)] focus-visible:ring-2 focus-visible:ring-[var(--accent-soft)]";
const inputClass = `w-full ${controlClass}`;

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

function AutoTextarea({
  id,
  value,
  onChange,
  mono,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  mono?: boolean;
}) {
  const ref = useAutoHeight(value);
  return (
    <textarea
      id={id}
      ref={ref}
      rows={3}
      value={value}
      spellCheck={!mono}
      onChange={(e) => onChange(e.target.value)}
      className={`${inputClass} resize-none leading-relaxed ${mono ? "font-mono text-[12.5px]" : ""}`}
    />
  );
}

function FieldLabel({ htmlFor, children, hint }: { htmlFor?: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-1.5 flex items-baseline justify-between gap-3">
      <label htmlFor={htmlFor} className="text-xs font-medium text-[var(--muted)]">
        {children}
      </label>
      {hint && <span className="text-xs text-[var(--muted)]">{hint}</span>}
    </div>
  );
}

// -- Collapsed summaries -----------------------------------------------------

function Arrow({ output, inputNames }: { output: string | null; inputNames: Set<string> }) {
  if (!output) return null;
  return (
    <>
      <span aria-label="saved as">→</span>
      <VarToken name={output} isInput={inputNames.has(output)} />
    </>
  );
}

function ArgPreview({ value, inputNames }: { value: unknown; inputNames: Set<string> }) {
  const ref = wholeReference(value);
  if (ref) return <VarToken name={ref} isInput={inputNames.has(ref.split(".")[0])} />;
  const text = formatValue(value).replace(/\s+/g, " ");
  return (
    <span className="font-mono text-[12px] text-[var(--fg)]">
      {text.length > ARG_PREVIEW_CHARS ? `${text.slice(0, ARG_PREVIEW_CHARS)}…` : text}
    </span>
  );
}

function StepSummary({ step, inputNames }: { step: WorkflowStep; inputNames: Set<string> }) {
  const output = stepOutput(step);
  if (step.kind === "tool") {
    const shown = Object.entries(step.args)
      .filter(([, value]) => typeof value === "string")
      .slice(0, 2);
    return (
      <>
        <span>{toolLabel(step.tool)}</span>
        {shown.map(([key, value]) => (
          <span key={key} className="inline-flex items-baseline gap-1">
            <span className="text-[var(--muted)]">{key}</span>
            <ArgPreview value={value} inputNames={inputNames} />
          </span>
        ))}
        <Arrow output={output} inputNames={inputNames} />
      </>
    );
  }
  if (step.kind === "script") {
    const lines = step.code.split("\n").length;
    return (
      <>
        <span>
          Python · {lines} {lines === 1 ? "line" : "lines"}
        </span>
        {Object.values(step.inputs).map((value, index) => (
          <ArgPreview key={index} value={value} inputNames={inputNames} />
        ))}
        <Arrow output={output} inputNames={inputNames} />
      </>
    );
  }
  if (step.kind === "llm") {
    return (
      <>
        <span>Returns</span>
        <span className="font-mono text-[12px] text-[var(--fg)]">{step.fields.map((f) => f.name).join(", ")}</span>
        <Arrow output={output} inputNames={inputNames} />
      </>
    );
  }
  if (step.kind === "check") {
    return (
      <>
        {step.conditions.map((condition, index) => (
          <span key={index} className="inline-flex items-baseline gap-1.5">
            {index > 0 && <span>and</span>}
            <ConditionText condition={condition} inputNames={inputNames} />
          </span>
        ))}
        <span>· otherwise stop</span>
      </>
    );
  }
  return (
    <>
      <span>Pauses for your approval</span>
      {step.when && (
        <span className="inline-flex items-baseline gap-1.5">
          · only when <ConditionText condition={step.when} inputNames={inputNames} />
        </span>
      )}
    </>
  );
}

// -- Editors ---------------------------------------------------------------------

function ArgControl({ id, value, onChange }: { id: string; value: unknown; onChange: (value: unknown) => void }) {
  if (typeof value === "boolean") {
    return (
      <select
        id={id}
        className={inputClass}
        value={value ? "yes" : "no"}
        onChange={(e) => onChange(e.target.value === "yes")}
      >
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    );
  }
  if (typeof value === "number") {
    return (
      <input
        id={id}
        type="number"
        className={inputClass}
        value={value}
        onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
      />
    );
  }
  if (typeof value === "string") {
    return value.includes("\n") ? (
      <AutoTextarea id={id} value={value} onChange={onChange} />
    ) : (
      <input id={id} className={inputClass} value={value} onChange={(e) => onChange(e.target.value)} />
    );
  }
  return (
    <div className="rounded-md bg-[var(--card-bg)] px-2.5 py-1.5 font-mono text-[12px] text-[var(--muted)]">
      {JSON.stringify(value)}
    </div>
  );
}

function ValuesHint({ names, inputNames }: { names: string[]; inputNames: Set<string> }) {
  if (names.length === 0) return null;
  return (
    <div className="mt-1.5 flex flex-wrap items-baseline gap-1.5 text-xs text-[var(--muted)]">
      <span>Available:</span>
      {names.map((name) => (
        <VarToken key={name} name={name} isInput={inputNames.has(name)} />
      ))}
      <span>
        -- write <span className="font-mono">{"{{name}}"}</span> to use one.
      </span>
    </div>
  );
}

function LLMEditor({ step, onChange, names, inputNames }: EditorProps<LLMStep> & { names: string[] }) {
  return (
    <>
      <div>
        <FieldLabel htmlFor={`${step.id}-prompt`}>Instructions</FieldLabel>
        <AutoTextarea
          id={`${step.id}-prompt`}
          value={step.prompt}
          onChange={(prompt) => onChange({ ...step, prompt })}
        />
        <ValuesHint names={names} inputNames={inputNames} />
      </div>
      <div>
        <FieldLabel hint="Anything else is sent back once, then the step fails">Must return</FieldLabel>
        <div className="overflow-hidden rounded-lg border border-[var(--border)]">
          <div className="grid grid-cols-[minmax(0,150px)_120px_minmax(0,1fr)] bg-[var(--card-bg)] text-xs text-[var(--muted)]">
            <div className="px-3 py-2">Field</div>
            <div className="px-3 py-2">Type</div>
            <div className="px-3 py-2">Meaning</div>
          </div>
          {step.fields.map((field, index) => {
            const update = (changes: Partial<typeof field>) =>
              onChange({ ...step, fields: step.fields.map((f, i) => (i === index ? { ...f, ...changes } : f)) });
            return (
              <div
                key={field.name}
                className="grid grid-cols-[minmax(0,150px)_120px_minmax(0,1fr)] items-center border-t border-[var(--border)] text-sm"
              >
                <div className="truncate px-3 py-1.5 font-mono text-[12.5px]">{field.name}</div>
                <div className="px-1.5 py-1">
                  <select
                    aria-label={`Type of ${field.name}`}
                    className={`${inputClass} border-transparent py-1`}
                    value={field.type}
                    onChange={(e) => update({ type: e.target.value as OutputFieldType })}
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
                    aria-label={`Meaning of ${field.name}`}
                    className={`${inputClass} border-transparent py-1`}
                    value={field.description}
                    placeholder="What it should contain"
                    onChange={(e) => update({ description: e.target.value })}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
      <div className="max-w-sm">
        <FieldLabel htmlFor={`${step.id}-model`}>Model</FieldLabel>
        <input
          id={`${step.id}-model`}
          className={inputClass}
          value={step.model ?? ""}
          placeholder="The task's model"
          onChange={(e) => onChange({ ...step, model: e.target.value.trim() || null })}
        />
      </div>
    </>
  );
}

function literalFromInput(raw: string, previous: unknown): unknown {
  if (typeof previous === "number") return raw.trim() === "" ? 0 : Number(raw);
  if (typeof previous === "boolean") return raw === "yes";
  return raw;
}

function ConditionEditor({
  condition,
  onChange,
  inputNames,
  label,
}: {
  condition: Condition;
  onChange: (condition: Condition) => void;
  inputNames: Set<string>;
  label: string;
}) {
  const right = condition.right;
  const ops = Object.keys(OP_LABEL).filter((op) => op !== "not_empty") as CheckOp[];
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm">
      <OperandText operand={condition.left} inputNames={inputNames} />
      {condition.op === "not_empty" && <span className="text-[var(--muted)]">{OP_LABEL.not_empty}</span>}
      {condition.op !== "not_empty" && (
        <select
          aria-label={`${label}: comparison`}
          className={`${controlClass} py-1`}
          value={condition.op}
          onChange={(e) => onChange({ ...condition, op: e.target.value as CheckOp })}
        >
          {ops.map((op) => (
            <option key={op} value={op}>
              {OP_LABEL[op]}
            </option>
          ))}
        </select>
      )}
      {right && "value" in right && typeof right.value === "boolean" && (
        <select
          aria-label={`${label}: value`}
          className={`${controlClass} py-1`}
          value={right.value ? "yes" : "no"}
          onChange={(e) => onChange({ ...condition, right: { value: e.target.value === "yes" } })}
        >
          <option value="yes">yes</option>
          <option value="no">no</option>
        </select>
      )}
      {right && "value" in right && typeof right.value !== "boolean" && (
        <input
          aria-label={`${label}: value`}
          className={`${controlClass} w-32 py-1`}
          type={typeof right.value === "number" ? "number" : "text"}
          value={String(right.value ?? "")}
          onChange={(e) => onChange({ ...condition, right: { value: literalFromInput(e.target.value, right.value) } })}
        />
      )}
      {right && !("value" in right) && <OperandText operand={right} inputNames={inputNames} />}
    </div>
  );
}

interface EditorProps<T extends WorkflowStep> {
  step: T;
  onChange: (step: T) => void;
  inputNames: Set<string>;
}

function StepFields({ step, onChange, inputNames, names }: EditorProps<WorkflowStep> & { names: string[] }) {
  if (step.kind === "tool") {
    return (
      <div>
        <FieldLabel>
          {toolLabel(step.tool)} <span className="font-normal">· with</span>
        </FieldLabel>
        <div className="grid grid-cols-[minmax(0,140px)_minmax(0,1fr)] items-start gap-x-3 gap-y-2">
          {Object.entries(step.args).map(([key, value]) => (
            <div key={key} className="contents">
              <label htmlFor={`${step.id}-${key}`} className="pt-1.5 font-mono text-[12.5px] text-[var(--muted)]">
                {key}
              </label>
              <ArgControl
                id={`${step.id}-${key}`}
                value={value}
                onChange={(next) => onChange({ ...step, args: { ...step.args, [key]: next } })}
              />
            </div>
          ))}
        </div>
        <ValuesHint names={names} inputNames={inputNames} />
      </div>
    );
  }
  if (step.kind === "script") {
    return (
      <div>
        <FieldLabel htmlFor={`${step.id}-code`} hint="Prints its result as JSON on the last line">
          Python
        </FieldLabel>
        <AutoTextarea id={`${step.id}-code`} value={step.code} mono onChange={(code) => onChange({ ...step, code })} />
        {Object.keys(step.inputs).length > 0 && (
          <div className="mt-1.5 flex flex-wrap items-baseline gap-1.5 text-xs text-[var(--muted)]">
            <span>Reads</span>
            {Object.entries(step.inputs).map(([key, value]) => (
              <span key={key} className="inline-flex items-baseline gap-1">
                <span className="font-mono">inputs["{key}"]</span>=<ArgPreview value={value} inputNames={inputNames} />
              </span>
            ))}
          </div>
        )}
      </div>
    );
  }
  if (step.kind === "llm") {
    return <LLMEditor step={step} onChange={onChange} names={names} inputNames={inputNames} />;
  }
  if (step.kind === "check") {
    return (
      <div className="flex flex-col gap-2">
        <FieldLabel hint="All must hold, or the run stops here">Checks</FieldLabel>
        {step.conditions.map((condition, index) => (
          <ConditionEditor
            key={index}
            label={`Check ${index + 1}`}
            condition={condition}
            inputNames={inputNames}
            onChange={(next) =>
              onChange({ ...step, conditions: step.conditions.map((c, i) => (i === index ? next : c)) })
            }
          />
        ))}
      </div>
    );
  }
  return (
    <>
      <div>
        <FieldLabel htmlFor={`${step.id}-message`}>What to show you</FieldLabel>
        <AutoTextarea
          id={`${step.id}-message`}
          value={step.message}
          onChange={(message) => onChange({ ...step, message })}
        />
        <ValuesHint names={names} inputNames={inputNames} />
      </div>
      {step.when && (
        <div>
          <FieldLabel>Only pause when</FieldLabel>
          <ConditionEditor
            label="Condition"
            condition={step.when}
            inputNames={inputNames}
            onChange={(when) => onChange({ ...step, when })}
          />
        </div>
      )}
    </>
  );
}

// -- The list -------------------------------------------------------------------

function StepCard({
  step,
  index,
  workflow,
  inputNames,
  expanded,
  onToggle,
  onSave,
}: {
  step: WorkflowStep;
  index: number;
  workflow: Workflow;
  inputNames: Set<string>;
  expanded: boolean;
  onToggle: () => void;
  onSave: (step: WorkflowStep) => Promise<string | null>;
}) {
  const [draft, setDraft] = useState(step);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!expanded) {
      setDraft(step);
      setError(null);
    }
  }, [expanded, step]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(step);
  const names = namesBefore(workflow, index);

  const save = async () => {
    setSaving(true);
    const problem = await onSave(draft);
    setSaving(false);
    setError(problem);
    if (!problem) onToggle();
  };

  return (
    <li
      data-step={step.id}
      className={`relative rounded-xl border bg-[var(--bg)] transition-shadow ${
        expanded
          ? "border-[color-mix(in_srgb,var(--accent)_70%,transparent)] shadow-[0_6px_24px_rgba(0,0,0,0.07)]"
          : "border-[var(--border)] hover:border-[var(--border-hover)]"
      }`}
    >
      <button
        type="button"
        aria-expanded={expanded}
        className="flex w-full items-center gap-3.5 rounded-xl px-3.5 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-soft)]"
        onClick={onToggle}
      >
        <StepKindTile step={step} />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate text-sm font-medium">{step.title}</span>
          {!expanded && (
            <span className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 gap-y-1 text-[13px] text-[var(--muted)]">
              <StepSummary step={step} inputNames={inputNames} />
            </span>
          )}
        </span>
        {expanded && step.kind === "llm" && (
          <span className="shrink-0 text-xs text-[var(--muted)]">{step.model ?? "Task's model"} · temperature 0</span>
        )}
        <span className="w-5 shrink-0 text-right text-xs tabular-nums text-[var(--muted)]">{index + 1}</span>
        <ChevronDownIcon
          className={`h-4 w-4 shrink-0 text-[var(--muted)] transition-transform motion-reduce:transition-none ${expanded ? "rotate-180" : ""}`}
        />
      </button>
      {expanded && (
        <div className="flex flex-col gap-4 px-4 pb-4 pl-[58px]">
          <div className="max-w-md">
            <FieldLabel htmlFor={`${step.id}-title`}>Step name</FieldLabel>
            <input
              id={`${step.id}-title`}
              className={inputClass}
              value={draft.title}
              maxLength={120}
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            />
          </div>
          <StepFields step={draft} onChange={setDraft} inputNames={inputNames} names={names} />
          {error && (
            <p role="alert" className="text-sm text-[var(--danger)]">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="h-8 rounded-md border border-[var(--border)] px-3 text-sm hover:bg-[var(--card-bg)]"
              onClick={onToggle}
            >
              {dirty ? "Discard" : "Close"}
            </button>
            <button
              type="button"
              disabled={!dirty || saving || !draft.title.trim()}
              className="h-8 rounded-md bg-[var(--primary)] px-3 text-sm font-medium text-[var(--primary-fg)] hover:bg-[var(--primary-hover)] disabled:opacity-40"
              onClick={save}
            >
              {saving ? "Saving…" : "Save step"}
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

function InputsSection({ workflow }: { workflow: Workflow }) {
  if (workflow.inputs.length === 0) return null;
  return (
    <section>
      <SectionHeading>Inputs</SectionHeading>
      <ul className="flex flex-col gap-2">
        {workflow.inputs.map((input) => (
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
    </section>
  );
}

/** A workflow task's inputs and steps (the Edit artboard of
 * https://claude.ai/artifact/4G5JyZ3r4tMPF6QcFG3Vj1). One step open at a
 * time; each saves on its own, validated by the server against the whole
 * workflow. */
export function WorkflowEditor({
  task,
  workflow,
  onSaved,
  focusStepId,
}: {
  task: ScheduledTask;
  workflow: Workflow;
  onSaved: () => void;
  focusStepId?: string | null;
}) {
  const [expandedId, setExpandedId] = useState<string | null>(focusStepId ?? null);
  const inputNames = new Set(workflow.inputs.map((input) => input.name));
  const listRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    if (!focusStepId) return;
    setExpandedId(focusStepId);
    listRef.current
      ?.querySelector(`[data-step="${focusStepId}"]`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focusStepId]);

  const saveStep = async (updated: WorkflowStep): Promise<string | null> => {
    const next: Workflow = {
      ...workflow,
      steps: workflow.steps.map((step) => (step.id === updated.id ? updated : step)),
    };
    const result = await updateScheduledTask(task.trigger_id, taskPayload(task, { workflow: next }));
    if ("error" in result) return result.error;
    onSaved();
    return null;
  };

  return (
    <div className="flex flex-col gap-7">
      <InputsSection workflow={workflow} />
      <section>
        <SectionHeading aside={<KindLegend />}>Steps</SectionHeading>
        <ol ref={listRef} className="relative flex flex-col gap-2">
          <span aria-hidden="true" className="absolute bottom-6 left-[27px] top-6 w-px bg-[var(--border)]" />
          {workflow.steps.map((step, index) => (
            <StepCard
              key={step.id}
              step={step}
              index={index}
              workflow={workflow}
              inputNames={inputNames}
              expanded={expandedId === step.id}
              onToggle={() => setExpandedId((current) => (current === step.id ? null : step.id))}
              onSave={saveStep}
            />
          ))}
        </ol>
      </section>
    </div>
  );
}
