import { describe, expect, it } from "vitest";
import type { ScheduledRun } from "../types/settings";
import type { StepRecord, Workflow } from "../types/workflow";
import { passShowing, runMap } from "./workflowRunMap";

const workflow: Workflow = {
  version: 1,
  inputs: [],
  steps: [
    { kind: "tool", id: "list", title: "List", tool: "list_files", args: {}, save_as: "files" },
    {
      kind: "loop",
      id: "each",
      title: "Each",
      over: "files",
      item: "file",
      max_items: 50,
      steps: [
        {
          kind: "branch",
          id: "big",
          title: "Big?",
          condition: { left: { ref: "file" }, op: "not_empty", right: null },
          then: [{ kind: "tool", id: "read", title: "Read", tool: "read_file", args: {} }],
          otherwise: [{ kind: "approval", id: "ask", title: "Ask", message: "" }],
        },
      ],
    },
    {
      kind: "branch",
      id: "any",
      title: "Any?",
      condition: { left: { ref: "files" }, op: "not_empty", right: null },
      then: [{ kind: "tool", id: "sum", title: "Sum", tool: "read_file", args: {} }],
      otherwise: [{ kind: "tool", id: "none", title: "None", tool: "read_file", args: {} }],
    },
  ],
} as unknown as Workflow;

const record = (step_id: string, status: StepRecord["status"], iteration: number[] = [], output: unknown = null) =>
  ({
    step_id,
    status,
    iteration,
    output,
    started_at: null,
    finished_at: null,
    error: null,
    checks: null,
    note: null,
    permission: null,
    agent: null,
  }) as StepRecord;

const run = (status: ScheduledRun["status"], steps: StepRecord[]) =>
  ({
    run_id: "r",
    thread_id: "t",
    started_at: "",
    source: "manual",
    status,
    finished_at: null,
    error: null,
    steps,
    inputs: {},
  }) as ScheduledRun;

describe("a run drawn on the canvas", () => {
  it("counts a loop's items and its steps' passes", () => {
    const states = runMap(
      workflow,
      run("running", [
        record("list", "done"),
        record("each", "running", [], { done: 1, total: 3, items: [] }),
        record("big", "done", [0], { arm: "then" }),
        record("read", "done", [0]),
        record("big", "running", [1]),
      ]),
    );
    expect(states.get("list")).toEqual({ status: "done" });
    expect(states.get("each")).toEqual({ status: "running", passes: { done: 1, total: 3 } });
    expect(states.get("big")).toMatchObject({ status: "running", arms: ["then"] });
    expect(states.get("ask")?.passes).toEqual({ done: 0, total: null });
    // Under a branch: only the passes that take its arm run it.
    expect(states.get("read")).toEqual({ status: "done", passes: { done: 1, total: null } });
    expect(states.get("big")?.passes).toEqual({ done: 1, total: 3 });
    // A pass still running may yet take the other arm.
    expect(states.get("ask")?.status).toBe("not_reached");
    expect(states.get("sum")?.status).toBe("not_reached");
  });

  it("marks the arm a branch went past as not taken", () => {
    const states = runMap(
      workflow,
      run("completed", [
        record("list", "done"),
        record("each", "done", [], { done: 0, total: 0, items: [] }),
        record("any", "done", [], { arm: "otherwise" }),
        record("none", "done"),
      ]),
    );
    expect(states.get("sum")?.status).toBe("not_taken");
    expect(states.get("none")?.status).toBe("done");
    expect(states.get("any")?.arms).toEqual(["otherwise"]);
  });

  it("shows the pass that needs the person over the ones that went well", () => {
    const states = runMap(
      workflow,
      run("needs_approval", [
        record("each", "waiting", [], { done: 1, total: 2, items: [] }),
        record("big", "done", [0], { arm: "otherwise" }),
        record("ask", "done", [0]),
        record("big", "done", [1], { arm: "otherwise" }),
        record("ask", "waiting", [1]),
      ]),
    );
    expect(states.get("ask")).toEqual({ status: "waiting", passes: { done: 1, total: null } });
    // Not taken by any pass yet, but the loop isn't through.
    expect(states.get("read")?.status).toBe("not_reached");
  });

  it("marks an arm no pass took once the loop is through", () => {
    const states = runMap(
      workflow,
      run("running", [
        record("each", "done", [], { done: 2, total: 2, items: [] }),
        record("big", "done", [0], { arm: "then" }),
        record("big", "done", [1], { arm: "then" }),
      ]),
    );
    expect(states.get("ask")?.status).toBe("not_taken");
  });

  it("calls a step left running by a stopped run stopped", () => {
    const states = runMap(workflow, run("stopped", [record("list", "running")]));
    expect(states.get("list")?.status).toBe("stopped");
  });
});

describe("the pass a picked step is shown in", () => {
  const steps = [record("why", "done", [0, 1]), record("why", "done", [0, 3]), record("why", "done", [2, 0])];
  const picked = run("completed", steps);
  it("is the last pass of that loop it ran in", () => {
    expect(passShowing(picked, "why", [])).toBe(2);
    expect(passShowing(picked, "why", [0])).toBe(3);
  });
  it("is none where it never ran", () => {
    expect(passShowing(picked, "why", [1])).toBeNull();
    expect(passShowing(picked, "read", [])).toBeNull();
  });
});
