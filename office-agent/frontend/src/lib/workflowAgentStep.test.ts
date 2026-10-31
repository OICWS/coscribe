import { describe, expect, it } from "vitest";
import type { AgentStep, Workflow } from "../types/workflow";
import { newStep, renameValue } from "./workflowEdit";
import { countModelSteps, modelStepsRead } from "./workflowLabels";
import { ROOT, scopeAt, scopes } from "./workflowTree";

const agent = (changes: Partial<AgentStep> = {}): AgentStep => ({
  kind: "agent",
  id: "research",
  title: "Research the company",
  autonomy: "bounded",
  goal: "Find the latest results of {{company}}",
  tools: ["search_web"],
  folders: [],
  sites: [],
  fields: [],
  max_steps: 20,
  model: null,
  save_as: "found",
  trusted_input: false,
  irreversible: false,
  ...changes,
});

const workflow = (step: AgentStep): Workflow => ({
  version: 2,
  inputs: [{ name: "company", label: "Company", type: "text", default: null }],
  steps: [step, { kind: "check", id: "check", title: "Check", conditions: [] }],
});

describe("agent steps", () => {
  it("make their report readable when they declare no fields", () => {
    const names = scopeAt(scopes(workflow(agent())), ROOT, 1);
    expect(names).toEqual(["company", "found", "found.report"]);
  });

  it("make their fields readable when they declare some", () => {
    const step = agent({ fields: [{ name: "revenue", type: "number", description: "" }] });
    expect(scopeAt(scopes(workflow(step)), ROOT, 1)).toContain("found.revenue");
    expect(scopeAt(scopes(workflow(step)), ROOT, 1)).not.toContain("found.report");
  });

  it("make nothing readable when their result isn't saved", () => {
    expect(scopeAt(scopes(workflow(agent({ save_as: null }))), ROOT, 1)).toEqual(["company"]);
  });

  it("follow a renamed input in their goal", () => {
    const renamed = renameValue(workflow(agent()), "company", "firm", null);
    expect((renamed.steps[0] as AgentStep).goal).toBe("Find the latest results of {{firm}}");
  });

  it("start bounded with no tools, so saving one asks for its tools", () => {
    const step = newStep("agent", workflow(agent()), ["company"]);
    expect(step).toMatchObject({ kind: "agent", autonomy: "bounded", tools: [], fields: [], trusted_input: false });
  });

  it("count as using a model, and as where a failed check's values came from", () => {
    const wf = workflow(agent());
    expect(countModelSteps(wf)).toBe(1);
    const read = modelStepsRead(wf, [{ left: { ref: "found.report" }, op: "not_empty", right: null }]);
    expect(read.map((step) => step.id)).toEqual(["research"]);
  });
});
