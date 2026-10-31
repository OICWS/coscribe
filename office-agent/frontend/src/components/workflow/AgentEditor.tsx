import { controlClass, inputClass } from "../../lib/formStyles";
import { toolLabel } from "../../lib/toolLabels";
import { UNAVAILABLE_TOOLS } from "../../lib/workflowEdit";
import { AUTONOMY_DESCRIPTION, AUTONOMY_LABEL } from "../../lib/workflowLabels";
import type { AgentStep, Autonomy } from "../../types/workflow";
import { ToggleSwitch } from "../ToggleSwitch";
import { type EditorProps, IrreversibleToggle, OutputFieldsTable, SaveAsField, ValuesHint } from "./editorParts";
import { AddRowButton, AutoTextarea, FieldLabel, RemoveButton } from "./fields";

// Mirrors workflow_spec/spec.py's MAX_AGENT_STEPS.
const MAX_AGENT_STEPS = 60;

const AUTONOMIES: Autonomy[] = ["bounded", "high", "full"];

function TextList({
  id,
  label,
  values,
  placeholder,
  onChange,
}: {
  id: string;
  label: string;
  values: string[];
  placeholder: string;
  onChange: (values: string[]) => void;
}) {
  return (
    <div>
      <FieldLabel>{label}</FieldLabel>
      <div className="flex flex-col gap-1.5">
        {values.map((value, index) => (
          <div key={index} className="flex items-center gap-1.5">
            <input
              id={`${id}-${index}`}
              aria-label={`${label} ${index + 1}`}
              spellCheck={false}
              className={`${inputClass} font-mono text-[12.5px]`}
              value={value}
              placeholder={placeholder}
              onChange={(e) => onChange(values.map((v, i) => (i === index ? e.target.value : v)))}
            />
            <RemoveButton
              label={`Remove ${value || `${label} ${index + 1}`}`}
              onClick={() => onChange(values.filter((_, i) => i !== index))}
            />
          </div>
        ))}
        <AddRowButton onClick={() => onChange([...values, ""])}>Add</AddRowButton>
      </div>
    </div>
  );
}

function ToolPicker({ step, onChange, context }: EditorProps<AgentStep>) {
  const offered = [...context.tools.values()]
    .filter((tool) => !UNAVAILABLE_TOOLS.has(tool.name) && !step.tools.includes(tool.name))
    .sort((a, b) => toolLabel(a.name).localeCompare(toolLabel(b.name)));
  return (
    <div>
      <FieldLabel htmlFor={`${step.id}-add-tool`} hint="The only tools it can call">
        Tools
      </FieldLabel>
      <div className="flex flex-wrap items-center gap-1.5">
        {step.tools.map((name) => (
          <span
            key={name}
            className="inline-flex items-center gap-0.5 rounded-md border border-[var(--border)] bg-[var(--card-bg)] py-0.5 pl-2 text-[13px]"
          >
            {toolLabel(name)}
            <RemoveButton
              label={`Remove ${toolLabel(name)}`}
              onClick={() => onChange({ ...step, tools: step.tools.filter((tool) => tool !== name) })}
            />
          </span>
        ))}
        <select
          id={`${step.id}-add-tool`}
          className={`${controlClass} w-56`}
          value=""
          onChange={(e) => e.target.value && onChange({ ...step, tools: [...step.tools, e.target.value] })}
        >
          <option value="">Add a tool…</option>
          {offered.map((tool) => (
            <option key={tool.name} value={tool.name}>
              {toolLabel(tool.name)}
            </option>
          ))}
        </select>
      </div>
      {step.tools.length === 0 && <p className="mt-1 text-xs text-[var(--danger)]">List at least one tool.</p>}
    </div>
  );
}

function AutonomyChoice({ step, onChange }: Omit<EditorProps<AgentStep>, "context">) {
  const setAutonomy = (autonomy: Autonomy) =>
    onChange({
      ...step,
      autonomy,
      // The server refuses a tool list on a broad agent and fields on a whole-task one.
      tools: autonomy === "bounded" ? step.tools : [],
      fields: autonomy === "full" ? [] : step.fields,
    });
  return (
    <fieldset>
      <legend className="mb-1.5 text-xs font-medium text-[var(--muted)]">How far it goes on its own</legend>
      <div className="flex flex-col gap-1.5">
        {AUTONOMIES.map((autonomy) => (
          <label key={autonomy} className="flex cursor-pointer items-start gap-2.5 text-sm">
            <input
              type="radio"
              className="mt-1"
              name={`${step.id}-autonomy`}
              checked={step.autonomy === autonomy}
              onChange={() => setAutonomy(autonomy)}
            />
            <span>
              <span className="font-medium">{AUTONOMY_LABEL[autonomy]}</span>
              <span className="block text-[13px] text-[var(--muted)]">{AUTONOMY_DESCRIPTION[autonomy]}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function TrustToggle({ step, onChange }: Omit<EditorProps<AgentStep>, "context">) {
  return (
    <label className="flex items-start gap-2.5 text-sm">
      <ToggleSwitch
        on={step.trusted_input}
        label="Act on what it reads from outside"
        onClick={() => onChange({ ...step, trusted_input: !step.trusted_input })}
      />
      <span>
        Act on what it reads from outside
        <span className="block text-[13px] text-[var(--muted)]">
          Off: when a value it’s given came from outside, such as a web page or a connector’s data, it can only read, so
          text in that value can’t make it change anything.
        </span>
      </span>
    </label>
  );
}

/** An agent step (decision 0028): a goal, how far it may go on its own, and
 * the limits that saving consents to. */
export function AgentEditor({ step, onChange, context }: EditorProps<AgentStep>) {
  return (
    <>
      <div>
        <FieldLabel htmlFor={`${step.id}-goal`}>Goal</FieldLabel>
        <AutoTextarea
          id={`${step.id}-goal`}
          value={step.goal}
          placeholder="What the agent should get done, from which values"
          onChange={(goal) => onChange({ ...step, goal })}
        />
        <ValuesHint context={context} />
      </div>
      <AutonomyChoice step={step} onChange={onChange} />
      {step.autonomy === "bounded" && <ToolPicker step={step} onChange={onChange} context={context} />}
      <div className="grid gap-4 sm:grid-cols-2">
        <TextList
          id={`${step.id}-folder`}
          label="Folders it may change"
          values={step.folders}
          placeholder="C:\Users\you\Reports"
          onChange={(folders) => onChange({ ...step, folders })}
        />
        <TextList
          id={`${step.id}-site`}
          label="Sites it may open"
          values={step.sites}
          placeholder="example.com"
          onChange={(sites) => onChange({ ...step, sites })}
        />
      </div>
      {step.autonomy !== "full" && (
        <OutputFieldsTable
          fields={step.fields}
          minimum={0}
          hint="None: its result is its report"
          onChange={(fields) => onChange({ ...step, fields })}
        >
          Returns
        </OutputFieldsTable>
      )}
      <TrustToggle step={step} onChange={onChange} />
      <IrreversibleToggle step={step} onChange={onChange} />
      <div className="flex flex-wrap gap-4">
        <div className="w-32">
          <FieldLabel htmlFor={`${step.id}-max-steps`}>Most model calls</FieldLabel>
          <input
            id={`${step.id}-max-steps`}
            type="number"
            min={1}
            max={MAX_AGENT_STEPS}
            className={inputClass}
            value={step.max_steps}
            onChange={(e) =>
              onChange({
                ...step,
                max_steps: Math.min(MAX_AGENT_STEPS, Math.max(1, Math.round(Number(e.target.value) || 1))),
              })
            }
          />
        </div>
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
