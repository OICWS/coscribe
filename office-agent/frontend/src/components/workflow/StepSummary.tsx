import { toolLabel } from "../../lib/toolLabels";
import { formatValue, stepOutput, wholeReference } from "../../lib/workflowLabels";
import type { WorkflowStep } from "../../types/workflow";
import { ConditionText, VarToken } from "./parts";

const ARG_PREVIEW_CHARS = 32;

function Arrow({ output, inputNames }: { output: string | null; inputNames: Set<string> }) {
  if (!output) return null;
  return (
    <>
      <span aria-label="saved as">→</span>
      <VarToken name={output} isInput={inputNames.has(output)} />
    </>
  );
}

export function ArgPreview({ value, inputNames }: { value: unknown; inputNames: Set<string> }) {
  const ref = wholeReference(value);
  if (ref) return <VarToken name={ref} isInput={inputNames.has(ref.split(".")[0])} />;
  const text = formatValue(value).replace(/\s+/g, " ");
  return (
    <span className="font-mono text-[12px] text-[var(--fg)]">
      {text.length > ARG_PREVIEW_CHARS ? `${text.slice(0, ARG_PREVIEW_CHARS)}…` : text}
    </span>
  );
}

export function StepSummary({ step, inputNames }: { step: WorkflowStep; inputNames: Set<string> }) {
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
