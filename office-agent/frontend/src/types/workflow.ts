// Mirrors src/coscribe/workflows/spec.py (the definition) and
// engine.py's StepRecord (one step of one run).

export type StepKind = "tool" | "script" | "llm" | "check" | "approval" | "branch" | "loop";
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

export interface BranchStep extends StepBase {
  kind: "branch";
  condition: Condition;
  then: WorkflowStep[];
  otherwise: WorkflowStep[];
}

export interface LoopStep extends StepBase {
  kind: "loop";
  /** A reference to the list to go through. */
  over: string;
  item: string;
  steps: WorkflowStep[];
  /** What to keep from each pass; the list of them is saved as save_as. */
  collect: string | null;
  save_as: string | null;
  max_items: number;
}

export type WorkflowStep = ToolStep | ScriptStep | LLMStep | CheckStep | ApprovalStep | BranchStep | LoopStep;
export type BlockStep = BranchStep | LoopStep;

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
  /** Which pass of each loop around the step, outermost first. */
  iteration?: number[];
}
