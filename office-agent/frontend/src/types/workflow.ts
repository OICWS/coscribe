// Mirrors src/coscribe/workflows/spec.py (the definition) and
// engine.py's StepRecord (one step of one run).

export type StepKind = "tool" | "script" | "llm" | "check" | "approval";
export type OutputFieldType = "text" | "number" | "boolean" | "list";
export type CheckOp = "eq" | "ne" | "gt" | "ge" | "lt" | "le" | "contains" | "not_empty";

/** Exactly one key is present. */
export type Operand = { ref: string } | { count: string } | { value: unknown };

export interface Condition {
  left: Operand;
  op: CheckOp;
  right?: Operand | null;
}

export interface WorkflowInput {
  name: string;
  label: string;
  type: "text" | "file" | "number";
  default: string | number | null;
}

export interface OutputField {
  name: string;
  type: OutputFieldType;
  description: string;
}

interface StepBase {
  id: string;
  title: string;
}

export interface ToolStep extends StepBase {
  kind: "tool";
  tool: string;
  args: Record<string, unknown>;
  save_as: string | null;
}

export interface ScriptStep extends StepBase {
  kind: "script";
  code: string;
  inputs: Record<string, unknown>;
  save_as: string | null;
}

export interface LLMStep extends StepBase {
  kind: "llm";
  prompt: string;
  fields: OutputField[];
  model: string | null;
  save_as: string;
}

export interface CheckStep extends StepBase {
  kind: "check";
  conditions: Condition[];
}

export interface ApprovalStep extends StepBase {
  kind: "approval";
  message: string;
  when: Condition | null;
}

export type WorkflowStep = ToolStep | ScriptStep | LLMStep | CheckStep | ApprovalStep;

export interface Workflow {
  version: 1;
  inputs: WorkflowInput[];
  steps: WorkflowStep[];
}

export type StepStatus = "pending" | "running" | "done" | "failed" | "skipped" | "waiting";

export interface CheckResult {
  held: boolean;
  left: unknown;
  right: unknown;
}

export interface StepRecord {
  step_id: string;
  status: StepStatus;
  started_at: string | null;
  finished_at: string | null;
  output: unknown;
  error: string | null;
  checks: CheckResult[] | null;
  /** What the person who answered an approval step wrote. */
  note?: string | null;
}
