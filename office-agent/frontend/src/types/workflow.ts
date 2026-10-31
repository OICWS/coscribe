// Mirrors src/coscribe/workflow_spec/spec.py (the definition) and
// engine.py's StepRecord (one step of one run), which comes from wire.gen.ts.

import type { WorkflowCheck, WorkflowStepRecord } from "./wire.gen";

export type StepKind = "tool" | "script" | "llm" | "agent" | "check" | "approval" | "branch" | "loop";
export type Autonomy = "bounded" | "high" | "full";
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
  /** Absent means required. */
  required?: boolean;
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
  /** Sends, submits, pays or deletes outside this machine: a test stops before it. */
  irreversible: boolean;
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

/** Decision 0028. "bounded" may use only `tools`, `folders` and `sites`. */
export interface AgentStep extends StepBase {
  kind: "agent";
  autonomy: Autonomy;
  goal: string;
  tools: string[];
  folders: string[];
  sites: string[];
  /** Empty means the result is { report: <the agent's reply> }. */
  fields: OutputField[];
  max_steps: number;
  model: string | null;
  save_as: string | null;
  /** The person said what this step reads from outside may be acted on. */
  trusted_input: boolean;
  /** As a tool step's. */
  irreversible: boolean;
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

export type WorkflowStep =
  | ToolStep
  | ScriptStep
  | LLMStep
  | AgentStep
  | CheckStep
  | ApprovalStep
  | BranchStep
  | LoopStep;
export type BlockStep = BranchStep | LoopStep;

export interface Workflow {
  /** 2 may hold agent steps. */
  version: 1 | 2;
  inputs: WorkflowInput[];
  steps: WorkflowStep[];
}

export type StepRecord = WorkflowStepRecord;
export type StepStatus = StepRecord["status"];
export type CheckResult = WorkflowCheck;
