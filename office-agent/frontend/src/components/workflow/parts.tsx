import type { ReactNode } from "react";
import { KIND_COLOR, KIND_LABEL, OP_LABEL, formatValue, splitTemplate } from "../../lib/workflowLabels";
import type { Condition, Operand, StepKind, WorkflowStep } from "../../types/workflow";
import { FileTextIcon, SearchIcon, ShieldCheckIcon, SparkIcon, TerminalIcon, ToolIcon, UserIcon } from "../icons";

function StepIcon({ step, className }: { step: WorkflowStep; className: string }) {
  if (step.kind === "llm") return <SparkIcon className={className} />;
  if (step.kind === "check") return <ShieldCheckIcon className={className} />;
  if (step.kind === "approval") return <UserIcon className={className} />;
  if (step.kind === "script") return <TerminalIcon className={className} />;
  if (step.tool.startsWith("search")) return <SearchIcon className={className} />;
  if (/^(write|edit|fill|add|format)_/.test(step.tool)) return <FileTextIcon className={className} />;
  return <ToolIcon className={className} />;
}

const KIND_ICON: Record<StepKind, (props: { className: string }) => ReactNode> = {
  tool: ToolIcon,
  script: TerminalIcon,
  llm: SparkIcon,
  check: ShieldCheckIcon,
  approval: UserIcon,
};

/** A kind's tile before there's a step to show (the add-step menu). */
export function KindTile({ kind }: { kind: StepKind }) {
  const color = KIND_COLOR[kind];
  const Glyph = KIND_ICON[kind];
  return (
    <span
      aria-hidden="true"
      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg"
      style={{ color, backgroundColor: `color-mix(in srgb, ${color} 13%, transparent)` }}
    >
      <Glyph className="h-[15px] w-[15px]" />
    </span>
  );
}

/** The colored square that says what kind of step this is. */
export function StepKindTile({ step, size = "md" }: { step: WorkflowStep; size?: "sm" | "md" }) {
  const color = KIND_COLOR[step.kind];
  return (
    <span
      aria-hidden="true"
      className={`flex shrink-0 items-center justify-center ${size === "md" ? "h-7 w-7 rounded-lg" : "h-5 w-5 rounded-md"}`}
      style={{ color, backgroundColor: `color-mix(in srgb, ${color} 13%, transparent)` }}
    >
      <StepIcon step={step} className={size === "md" ? "h-[15px] w-[15px]" : "h-3 w-3"} />
    </span>
  );
}

export function KindLegend() {
  const kinds: StepKind[] = ["tool", "script", "llm", "check", "approval"];
  return (
    <div className="flex flex-wrap items-center gap-x-3.5 gap-y-1 text-xs text-[var(--muted)]">
      {kinds.map((kind) => (
        <span key={kind} className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-[3px]" style={{ backgroundColor: KIND_COLOR[kind] }} />
          {KIND_LABEL[kind]}
        </span>
      ))}
    </div>
  );
}

/** A value by name. Inputs (what you give each run) are tinted; values
 * earlier steps produce are neutral. */
export function VarToken({ name, isInput }: { name: string; isInput: boolean }) {
  return (
    <span
      className={`inline-block max-w-full truncate rounded-[5px] px-1.5 py-px align-baseline font-mono text-[12px] leading-[1.45] ${
        isInput ? "bg-[var(--accent-soft)] text-[var(--accent-ink)]" : "bg-[var(--card-bg)] text-[var(--fg)]"
      }`}
    >
      {name}
    </span>
  );
}

export function TemplateText({ text, inputNames }: { text: string; inputNames: Set<string> }) {
  return (
    <>
      {splitTemplate(text).map((part, index) =>
        "ref" in part ? (
          <VarToken key={index} name={part.ref} isInput={inputNames.has(part.ref.split(".")[0])} />
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </>
  );
}

export function OperandText({ operand, inputNames }: { operand: Operand; inputNames: Set<string> }) {
  if ("ref" in operand) return <VarToken name={operand.ref} isInput={inputNames.has(operand.ref.split(".")[0])} />;
  if ("count" in operand) {
    return (
      <>
        count of <VarToken name={operand.count} isInput={inputNames.has(operand.count.split(".")[0])} />
      </>
    );
  }
  return <span className="font-mono text-[12.5px]">{formatValue(operand.value)}</span>;
}

export function ConditionText({ condition, inputNames }: { condition: Condition; inputNames: Set<string> }) {
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-1.5 gap-y-1">
      <OperandText operand={condition.left} inputNames={inputNames} />
      <span className="text-[var(--muted)]">{OP_LABEL[condition.op]}</span>
      {condition.right && <OperandText operand={condition.right} inputNames={inputNames} />}
    </span>
  );
}

export function SectionHeading({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-2.5 flex flex-wrap items-baseline justify-between gap-2">
      <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--muted)]">{children}</h2>
      {aside}
    </div>
  );
}
