import type { CheckOp, Condition, Operand, StepKind, Workflow, WorkflowStep } from "../types/workflow";

export const KIND_LABEL: Record<StepKind, string> = {
  tool: "Tool",
  script: "Script",
  llm: "Model",
  check: "Check",
  approval: "Approval",
};

export const KIND_COLOR: Record<StepKind, string> = {
  tool: "var(--kind-tool)",
  script: "var(--kind-script)",
  llm: "var(--kind-llm)",
  check: "var(--kind-check)",
  approval: "var(--kind-approval)",
};

export const OP_LABEL: Record<CheckOp, string> = {
  eq: "=",
  ne: "≠",
  gt: ">",
  ge: "≥",
  lt: "<",
  le: "≤",
  contains: "contains",
  not_empty: "is not empty",
};

const NEGATED_OP: Record<CheckOp, string> = {
  eq: "≠",
  ne: "=",
  gt: "≤",
  ge: "<",
  lt: "≥",
  le: ">",
  contains: "doesn't contain",
  not_empty: "is empty",
};

/** What a check actually found, e.g. "6 ≠ 5" or "0 ≤ 0". */
export function describeCheckResult(op: CheckOp, held: boolean, left: unknown, right: unknown): string {
  const symbol = held ? OP_LABEL[op] : NEGATED_OP[op];
  if (op === "not_empty") return held ? "has a value" : "is empty";
  return `${formatValue(left)} ${symbol} ${formatValue(right)}`;
}

export function stepConditions(step: WorkflowStep): Condition[] {
  if (step.kind === "check") return step.conditions;
  if (step.kind === "approval" && step.when) return [step.when];
  return [];
}

const TEMPLATE = /\{\{\s*([a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\s*\}\}/g;

export type TemplatePart = { text: string } | { ref: string };

/** "Search {{pdf_path}}" -> [{text: "Search "}, {ref: "pdf_path"}]. */
export function splitTemplate(text: string): TemplatePart[] {
  const parts: TemplatePart[] = [];
  let last = 0;
  for (const match of text.matchAll(TEMPLATE)) {
    const index = match.index ?? 0;
    if (index > last) parts.push({ text: text.slice(last, index) });
    parts.push({ ref: match[1] });
    last = index + match[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last) });
  return parts;
}

/** The one reference an argument is, if it's exactly one. */
export function wholeReference(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const parts = splitTemplate(value.trim());
  return parts.length === 1 && "ref" in parts[0] ? parts[0].ref : null;
}

export function stepOutput(step: WorkflowStep): string | null {
  return "save_as" in step ? step.save_as : null;
}

/** Names a step can read: the inputs, then what each earlier step saved. */
export function namesBefore(workflow: Workflow, index: number): string[] {
  const names = workflow.inputs.map((input) => input.name);
  for (const step of workflow.steps.slice(0, index)) {
    const output = stepOutput(step);
    if (output) names.push(output);
  }
  return names;
}

export function operandReference(operand: Operand): string | null {
  if ("ref" in operand) return operand.ref;
  if ("count" in operand) return operand.count;
  return null;
}

/** The model steps whose results a condition reads -- where a failed
 * check's bad data most likely came from. */
export function modelStepsRead(workflow: Workflow, conditions: Condition[]): WorkflowStep[] {
  const roots = new Set<string>();
  for (const condition of conditions) {
    for (const operand of [condition.left, condition.right]) {
      const ref = operand ? operandReference(operand) : null;
      if (ref) roots.add(ref.split(".")[0]);
    }
  }
  return workflow.steps.filter((step) => step.kind === "llm" && roots.has(step.save_as));
}

export function formatValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (value === null || value === undefined) return "nothing";
  if (Array.isArray(value)) return value.length <= 4 ? value.map(formatValue).join(", ") : `${value.length} items`;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function stepDuration(startedAt: string | null, finishedAt: string | null): string | null {
  if (!startedAt || !finishedAt) return null;
  const seconds = (new Date(finishedAt).getTime() - new Date(startedAt).getTime()) / 1000;
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  return seconds < 10 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds)}s`;
}

export function countModelSteps(workflow: Workflow): number {
  return workflow.steps.filter((step) => step.kind === "llm").length;
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isScalarValue(value: unknown): boolean {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

/** One line saying what a step produced. */
export function summarizeOutput(value: unknown): string {
  if (Array.isArray(value)) return `${value.length} ${value.length === 1 ? "result" : "results"}`;
  if (isPlainRecord(value)) {
    if (typeof value.path === "string") return `Saved ${value.path}`;
    const scalars = Object.entries(value).filter(([, item]) => isScalarValue(item));
    if (scalars.length > 0 && scalars.length <= 2) {
      return scalars.map(([key, item]) => `${key} = ${formatValue(item)}`).join(" · ");
    }
    return `${Object.keys(value).length} fields`;
  }
  if (typeof value === "string") {
    const line = value.split("\n")[0];
    return line.length > 80 ? `${line.slice(0, 80)}…` : line;
  }
  return value === null || value === undefined ? "" : formatValue(value);
}
