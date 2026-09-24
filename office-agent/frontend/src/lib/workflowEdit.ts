import type { ToolInfo } from "../types/settings";
import type { Condition, Operand, StepKind, Workflow, WorkflowStep } from "../types/workflow";
import { toolLabel } from "./toolLabels";
import { stepOutput } from "./workflowLabels";

// Mirrors workflows/spec.py's IDENTIFIER.
export const IDENTIFIER = /^[a-z][a-z0-9_]{0,39}$/;

// A workflow asks through an approval step instead (engine.py's
// UNAVAILABLE_TOOLS).
export const UNAVAILABLE_TOOLS = new Set(["ask_user_question", "create_scheduled_task"]);

export function toIdentifier(text: string, fallback: string): string {
  const slug = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^[^a-z]+/, "")
    .slice(0, 32);
  return slug || fallback;
}

function takenNames(workflow: Workflow): Set<string> {
  const names = new Set(workflow.inputs.map((input) => input.name));
  for (const step of workflow.steps) {
    const output = stepOutput(step);
    if (output) names.add(output);
  }
  return names;
}

function unique(base: string, taken: Set<string>): string {
  if (!taken.has(base)) return base;
  for (let n = 2; ; n++) {
    const candidate = `${base.slice(0, 36)}_${n}`;
    if (!taken.has(candidate)) return candidate;
  }
}

export function uniqueStepId(workflow: Workflow, base: string): string {
  return unique(toIdentifier(base, "step"), new Set(workflow.steps.map((step) => step.id)));
}

export function uniqueValueName(workflow: Workflow, base: string): string {
  return unique(toIdentifier(base, "result"), takenNames(workflow));
}

const NEW_TITLE: Record<StepKind, string> = {
  tool: "New tool step",
  script: "New script",
  llm: "Ask the model",
  check: "Check the values",
  approval: "Review before continuing",
};

const SCRIPT_TEMPLATE = `import json

# Values listed under "Reads" arrive in the inputs dict.
result = {}

print(json.dumps(result))`;

export function newStep(kind: StepKind, workflow: Workflow, tool?: ToolInfo): WorkflowStep {
  const title = tool ? toolLabel(tool.name) : NEW_TITLE[kind];
  const id = uniqueStepId(workflow, tool ? tool.name : kind);
  const saveAs = uniqueValueName(workflow, tool ? `${tool.name}_result` : "result");
  const firstValue = [...takenNames(workflow)][0];
  switch (kind) {
    case "tool": {
      const args: Record<string, unknown> = {};
      for (const param of tool?.params ?? []) {
        if (param.required) args[param.name] = param.type === "boolean" ? false : "";
      }
      // Only a read's result is usually worth keeping; a write's is a receipt.
      const keepsResult = tool?.risk_category === "READ";
      return { kind, id, title, tool: tool?.name ?? "", args, save_as: keepsResult ? saveAs : null };
    }
    case "script":
      return { kind, id, title, code: SCRIPT_TEMPLATE, inputs: {}, save_as: saveAs };
    case "llm":
      return {
        kind,
        id,
        title,
        prompt: "",
        fields: [{ name: "answer", type: "text", description: "" }],
        model: null,
        save_as: saveAs,
      };
    case "check":
      return {
        kind,
        id,
        title,
        conditions: [{ left: firstValue ? { ref: firstValue } : { value: "" }, op: "not_empty", right: null }],
      };
    case "approval":
      return { kind, id, title, message: "Check the results so far before the run continues.", when: null };
  }
}

export function moveStep(workflow: Workflow, index: number, delta: -1 | 1): Workflow {
  const target = index + delta;
  if (target < 0 || target >= workflow.steps.length) return workflow;
  const steps = [...workflow.steps];
  [steps[index], steps[target]] = [steps[target], steps[index]];
  return { ...workflow, steps };
}

// -- Renaming a value everywhere it's read ----------------------------------

function renameRoot(reference: string, from: string, to: string): string {
  if (reference === from) return to;
  return reference.startsWith(`${from}.`) ? `${to}${reference.slice(from.length)}` : reference;
}

function renameInText(text: string, from: string, to: string): string {
  return text.replace(/\{\{\s*([a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\s*\}\}/g, (whole, reference: string) =>
    renameRoot(reference, from, to) === reference ? whole : `{{${renameRoot(reference, from, to)}}}`,
  );
}

function renameInValue(value: unknown, from: string, to: string): unknown {
  if (typeof value === "string") return renameInText(value, from, to);
  if (Array.isArray(value)) return value.map((item) => renameInValue(item, from, to));
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, renameInValue(v, from, to)]));
  }
  return value;
}

function renameOperand(operand: Operand, from: string, to: string): Operand {
  if ("ref" in operand) return { ref: renameRoot(operand.ref, from, to) };
  if ("count" in operand) return { count: renameRoot(operand.count, from, to) };
  return operand;
}

function renameCondition(condition: Condition, from: string, to: string): Condition {
  return {
    ...condition,
    left: renameOperand(condition.left, from, to),
    right: condition.right ? renameOperand(condition.right, from, to) : condition.right,
  };
}

function renameInStep(step: WorkflowStep, from: string, to: string): WorkflowStep {
  switch (step.kind) {
    case "tool":
      return { ...step, args: renameInValue(step.args, from, to) as Record<string, unknown> };
    case "script":
      return { ...step, inputs: renameInValue(step.inputs, from, to) as Record<string, unknown> };
    case "llm":
      return { ...step, prompt: renameInText(step.prompt, from, to) };
    case "check":
      return { ...step, conditions: step.conditions.map((c) => renameCondition(c, from, to)) };
    case "approval":
      return {
        ...step,
        message: renameInText(step.message, from, to),
        when: step.when ? renameCondition(step.when, from, to) : null,
      };
  }
}

/** Every read of `from` in the steps after `afterIndex` now reads `to`,
 * so renaming a step's result (or an input, with afterIndex -1) doesn't
 * break the steps that use it. */
export function renameValue(workflow: Workflow, from: string, to: string, afterIndex: number): Workflow {
  if (from === to) return workflow;
  return {
    ...workflow,
    steps: workflow.steps.map((step, index) => (index > afterIndex ? renameInStep(step, from, to) : step)),
  };
}

/** Values a step can read, for pickers: inputs and earlier results, plus
 * the declared fields of earlier model steps. */
export function suggestionsBefore(workflow: Workflow, index: number): string[] {
  const names = workflow.inputs.map((input) => input.name);
  for (const step of workflow.steps.slice(0, index)) {
    const output = stepOutput(step);
    if (!output) continue;
    names.push(output);
    if (step.kind === "llm") names.push(...step.fields.map((field) => `${output}.${field.name}`));
  }
  return names;
}
