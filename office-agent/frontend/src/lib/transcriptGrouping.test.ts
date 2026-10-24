import { describe, expect, it } from "vitest";
import type { LogItem } from "../state/reducer";
import type { Workflow } from "../types/workflow";
import {
  blockedWriteOf,
  groupHasPendingApproval,
  groupToolRuns,
  groupTurns,
  isFailure,
  isRunning,
  latestWorkflowDraft,
  latestWorkflowDraftId,
  summarizeItem,
  workflowTests,
  type ToolRunGroup,
} from "./transcriptGrouping";

type Of<K extends LogItem["kind"]> = Extract<LogItem, { kind: K }>;

let seq = 0;
const nextId = () => `t${++seq}`;

function user(text = "hi", turnIndex = 0): Of<"user"> {
  return { id: nextId(), kind: "user", text, turnIndex };
}
function agent(text = "ok", streaming = false): Of<"agent"> {
  return { id: nextId(), kind: "agent", text, streaming };
}
function system(text = "note"): Of<"system"> {
  return { id: nextId(), kind: "system", text };
}
function tool(toolName: string, rest: Partial<Omit<Of<"tool">, "kind" | "toolName">> = {}): Of<"tool"> {
  return { id: nextId(), kind: "tool", toolName, arguments: {}, ...rest };
}
function approval(
  toolName: string,
  status: Of<"approval">["status"],
  rest: Partial<Omit<Of<"approval">, "kind" | "toolName" | "status">> = {},
): Of<"approval"> {
  return { id: nextId(), kind: "approval", toolName, arguments: {}, status, ...rest };
}

const workflow: Workflow = { version: 1, inputs: [], steps: [] };

function draftResult(extra: Record<string, unknown> = {}) {
  return { status: "drafted", workflow, ...extra };
}

describe("groupTurns", () => {
  it("returns no turns for an empty log", () => {
    expect(groupTurns([])).toEqual([]);
  });

  it("starts a turn at each user message and keeps everything until the next one", () => {
    const u1 = user("one", 0);
    const a1 = agent("first");
    const t1 = tool("read_file");
    const u2 = user("two", 1);
    const a2 = agent("second");
    const turns = groupTurns([u1, t1, a1, u2, a2]);
    expect(turns.map((t) => t.id)).toEqual([`turn-${u1.id}`, `turn-${u2.id}`]);
    expect(turns[0].items).toEqual([u1, t1, a1]);
    expect(turns[0].userItem).toBe(u1);
    expect(turns[1].items).toEqual([u2, a2]);
    expect(turns[1].userItem).toBe(u2);
  });

  it("takes the last agent message as the turn's final reply", () => {
    const first = agent("draft");
    const last = agent("final");
    const [turn] = groupTurns([user(), first, tool("read_file"), last]);
    expect(turn.finalAgentItem).toBe(last);
  });

  it("has no final reply while the turn has produced none", () => {
    const [turn] = groupTurns([user(), tool("read_file")]);
    expect(turn.finalAgentItem).toBeNull();
  });

  it("flags a turn that holds a pending approval, not an answered one", () => {
    const [pending] = groupTurns([user(), approval("write_file", "pending")]);
    const [approved] = groupTurns([user(), approval("write_file", "approved")]);
    const [denied] = groupTurns([user(), approval("write_file", "denied")]);
    expect(pending.hasPendingApproval).toBe(true);
    expect(approved.hasPendingApproval).toBe(false);
    expect(denied.hasPendingApproval).toBe(false);
  });

  it("scopes a pending approval to its own turn", () => {
    const turns = groupTurns([user("a", 0), approval("write_file", "pending"), user("b", 1), agent()]);
    expect(turns.map((t) => t.hasPendingApproval)).toEqual([true, false]);
  });

  it("puts items before the first user message in a lead turn with no user item", () => {
    const note = system("Compacted");
    const u = user();
    const turns = groupTurns([note, u]);
    expect(turns).toHaveLength(2);
    expect(turns[0].id).toBe(`turn-lead-${note.id}`);
    expect(turns[0].userItem).toBeNull();
    expect(turns[0].items).toEqual([note]);
    expect(turns[1].userItem).toBe(u);
  });
});

describe("groupToolRuns", () => {
  it("passes user, agent and system items through untouched", () => {
    const items = [user(), agent(), system()];
    expect(groupToolRuns(items)).toEqual(items);
  });

  it("wraps even a single tool call in a run", () => {
    const t = tool("read_file");
    const [entry] = groupToolRuns([t]);
    expect(entry).toEqual({ kind: "tool_run", id: `run-${t.id}`, items: [t] });
  });

  it("merges consecutive tool and approval items into one run", () => {
    const t1 = tool("read_file");
    const ap = approval("write_file", "approved");
    const t2 = tool("list_files");
    const entries = groupToolRuns([t1, ap, t2]);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ kind: "tool_run", id: `run-${t1.id}`, items: [t1, ap, t2] });
  });

  it("splits runs at a message between them", () => {
    const t1 = tool("read_file");
    const a = agent();
    const t2 = tool("list_files");
    const entries = groupToolRuns([t1, a, t2]);
    expect(entries.map((e) => e.kind)).toEqual(["tool_run", "agent", "tool_run"]);
  });

  it("shows a finished draft_workflow call as a draft card and ends the run around it", () => {
    const before = tool("read_file");
    const drafted = tool("draft_workflow", { result: draftResult({ name: "Weekly report" }) });
    const after = tool("list_files");
    const entries = groupToolRuns([before, drafted, after]);
    expect(entries.map((e) => e.kind)).toEqual(["tool_run", "workflow_draft", "tool_run"]);
    expect(entries[1]).toMatchObject({ kind: "workflow_draft", id: drafted.id, name: "Weekly report" });
  });

  it("keeps a draft_workflow call that has no result yet as an ordinary tool", () => {
    const entries = groupToolRuns([tool("draft_workflow")]);
    expect(entries.map((e) => e.kind)).toEqual(["tool_run"]);
  });

  it("keeps a draft_workflow call whose result is not a drafted workflow as an ordinary tool", () => {
    const failed = tool("draft_workflow", { result: { status: "invalid", workflow } });
    const noWorkflow = tool("draft_workflow", { result: { status: "drafted" } });
    const nullWorkflow = tool("draft_workflow", { result: { status: "drafted", workflow: null } });
    for (const t of [failed, noWorkflow, nullWorkflow]) {
      expect(groupToolRuns([t]).map((e) => e.kind)).toEqual(["tool_run"]);
    }
  });

  it("reads the draft out of a JSON string result, with defaults for missing fields", () => {
    const t = tool("revise_workflow", { result: JSON.stringify(draftResult()) });
    const [entry] = groupToolRuns([t]);
    expect(entry).toEqual({
      kind: "workflow_draft",
      id: t.id,
      name: "Untitled workflow",
      workflow,
      notes: [],
      environment: null,
      triggerId: null,
      changes: [],
      draftId: null,
    });
  });

  it("carries every field of a full draft onto the card", () => {
    const t = tool("revise_workflow", {
      result: draftResult({
        name: "Nightly sync",
        notes: ["a", 2],
        environment: "env_reports",
        trigger_id: "trg-1",
        changes: ["moved to 9:00"],
        draft_id: "d-1",
      }),
    });
    const [entry] = groupToolRuns([t]);
    expect(entry).toMatchObject({
      name: "Nightly sync",
      notes: ["a", "2"],
      environment: "env_reports",
      triggerId: "trg-1",
      changes: ["moved to 9:00"],
      draftId: "d-1",
    });
  });

  it("does not turn an approval item into a draft card", () => {
    const ap = approval("draft_workflow", "approved", { result: draftResult() });
    expect(groupToolRuns([ap]).map((e) => e.kind)).toEqual(["tool_run"]);
  });
});

describe("groupHasPendingApproval", () => {
  const run = (...items: ToolRunGroup["items"]): ToolRunGroup => ({ kind: "tool_run", id: "r", items });

  it("is true when any approval in the run is pending", () => {
    expect(groupHasPendingApproval(run(tool("read_file"), approval("write_file", "pending")))).toBe(true);
  });

  it("is false for answered approvals and plain tool calls", () => {
    expect(groupHasPendingApproval(run(tool("read_file"), approval("write_file", "approved")))).toBe(false);
    expect(groupHasPendingApproval(run(approval("write_file", "denied")))).toBe(false);
  });
});

describe("isFailure", () => {
  it("is true only when the call explicitly errored", () => {
    expect(isFailure(tool("read_file", { result: "x", isError: true }))).toBe(true);
    expect(isFailure(tool("read_file", { result: "x", isError: false }))).toBe(false);
    expect(isFailure(tool("read_file"))).toBe(false);
  });

  it("applies to approvals too", () => {
    expect(isFailure(approval("write_file", "approved", { isError: true }))).toBe(true);
    expect(isFailure(approval("write_file", "denied"))).toBe(false);
  });
});

describe("isRunning", () => {
  it("is false when no turn is live", () => {
    expect(isRunning(tool("read_file"), false)).toBe(false);
  });

  it("is true for a tool call without a result in a live turn", () => {
    expect(isRunning(tool("read_file"), true)).toBe(true);
  });

  it("is false once a result or an error has arrived", () => {
    expect(isRunning(tool("read_file", { result: "done" }), true)).toBe(false);
    expect(isRunning(tool("read_file", { isError: true }), true)).toBe(false);
  });

  it("runs an approval only after it is approved, never while pending or denied", () => {
    expect(isRunning(approval("write_file", "approved"), true)).toBe(true);
    expect(isRunning(approval("write_file", "pending"), true)).toBe(false);
    expect(isRunning(approval("write_file", "denied"), true)).toBe(false);
  });

  it("is false for an approved call that already has a result", () => {
    expect(isRunning(approval("write_file", "approved", { result: "ok" }), true)).toBe(false);
  });
});

describe("blockedWriteOf", () => {
  it("is null before the call has a result", () => {
    expect(blockedWriteOf(tool("run_python_script"))).toBeNull();
  });

  it("reads the blocked path and folder from an object result", () => {
    const t = tool("run_python_script", { result: { blocked_write: "D:\\x\\a.txt", blocked_folder: "D:\\x" } });
    expect(blockedWriteOf(t)).toEqual({ path: "D:\\x\\a.txt", folder: "D:\\x" });
  });

  it("reads a JSON string result", () => {
    const t = tool("run_python_script", { result: JSON.stringify({ blocked_write: "/a/b" }) });
    expect(blockedWriteOf(t)).toEqual({ path: "/a/b", folder: null });
  });

  it("has no folder to offer when blocked_folder is not a string", () => {
    const t = tool("run_python_script", { result: { blocked_write: "/a/b", blocked_folder: null } });
    expect(blockedWriteOf(t)).toEqual({ path: "/a/b", folder: null });
  });

  it("is null for results that are not a blocked write", () => {
    expect(blockedWriteOf(tool("x", { result: "not json" }))).toBeNull();
    expect(blockedWriteOf(tool("x", { result: 42 }))).toBeNull();
    expect(blockedWriteOf(tool("x", { result: null }))).toBeNull();
    expect(blockedWriteOf(tool("x", { result: { blocked_write: 5 } }))).toBeNull();
    expect(blockedWriteOf(tool("x", { result: { ok: true } }))).toBeNull();
  });
});

describe("workflowTests", () => {
  const testCall = (draftId: unknown, rest: Partial<Omit<Of<"tool">, "kind" | "toolName">> = {}) =>
    tool("test_workflow", { arguments: { draft_id: draftId }, ...rest });
  const passed = {
    status: "passed",
    seconds: 1.5,
    steps: [{ id: "s1", title: "Step", status: "done", seconds: 1, output: null, error: null }],
    failed_step: null,
    error: null,
  };

  it("ignores other tools and calls without a draft id", () => {
    const tests = workflowTests([tool("read_file"), testCall(undefined), testCall("")], true);
    expect(tests.size).toBe(0);
  });

  it("marks a call with no result as running while its turn is in flight", () => {
    const items = [user(), testCall("d1")];
    expect(workflowTests(items, true).get("d1")).toEqual({ result: null, running: true });
  });

  it("does not leave a call running when no turn is in flight (Stop cancels without a result)", () => {
    const items = [user(), testCall("d1")];
    expect(workflowTests(items, false).get("d1")).toEqual({ result: null, running: false });
  });

  it("does not call a test from an earlier turn running", () => {
    const items = [user("a", 0), testCall("d1"), user("b", 1)];
    expect(workflowTests(items, true).get("d1")).toEqual({ result: null, running: false });
  });

  it("runs an approval-gated test only once it is approved", () => {
    const gated = (status: Of<"approval">["status"]) =>
      approval("test_workflow", status, { arguments: { draft_id: "d1" } });
    expect(workflowTests([user(), gated("approved")], true).get("d1")?.running).toBe(true);
    expect(workflowTests([user(), gated("pending")], true).get("d1")?.running).toBe(false);
    expect(workflowTests([user(), gated("denied")], true).get("d1")?.running).toBe(false);
  });

  it("returns a finished result with files defaulted to an empty list", () => {
    const tests = workflowTests([user(), testCall("d1", { result: passed })], true);
    const entry = tests.get("d1");
    expect(entry?.running).toBe(false);
    expect(entry?.result).toMatchObject({ status: "passed", seconds: 1.5, files: [] });
    expect(entry?.result?.steps).toHaveLength(1);
  });

  it("keeps files the result already carries", () => {
    const files = [{ path: "out.xlsx", size: 10 }];
    const tests = workflowTests([testCall("d1", { result: { ...passed, files } })], false);
    expect(tests.get("d1")?.result?.files).toEqual(files);
  });

  it("reads a JSON string result", () => {
    const tests = workflowTests([testCall("d1", { result: JSON.stringify(passed) })], false);
    expect(tests.get("d1")?.result?.status).toBe("passed");
  });

  it("has no result when the output is not a test report", () => {
    const missingSteps = workflowTests([testCall("d1", { result: { status: "passed" } })], false);
    const missingStatus = workflowTests([testCall("d2", { result: { steps: [] } })], false);
    const error = workflowTests([testCall("d3", { result: "boom" })], false);
    expect(missingSteps.get("d1")).toEqual({ result: null, running: false });
    expect(missingStatus.get("d2")).toEqual({ result: null, running: false });
    expect(error.get("d3")).toEqual({ result: null, running: false });
  });

  it("keeps the latest call for a draft and tracks drafts separately", () => {
    const items = [
      user(),
      testCall("d1", { result: { ...passed, status: "failed" } }),
      testCall("d2", { result: passed }),
      testCall("d1", { result: passed }),
    ];
    const tests = workflowTests(items, false);
    expect([...tests.keys()].sort()).toEqual(["d1", "d2"]);
    expect(tests.get("d1")?.result?.status).toBe("passed");
  });
});

describe("latestWorkflowDraft", () => {
  it("is null when there is no draft", () => {
    expect(latestWorkflowDraft([])).toBeNull();
    expect(latestWorkflowDraft([user(), tool("read_file"), agent()])).toBeNull();
  });

  it("returns the newest draft, whichever tool made it", () => {
    const older = tool("draft_workflow", { result: draftResult({ name: "old" }) });
    const newer = tool("revise_workflow", { result: draftResult({ name: "new" }) });
    expect(latestWorkflowDraft([older, agent(), newer, agent()])?.name).toBe("new");
    expect(latestWorkflowDraft([older, agent()])?.name).toBe("old");
  });

  it("skips a newer call that did not produce a draft", () => {
    const good = tool("draft_workflow", { result: draftResult({ name: "good" }) });
    const bad = tool("revise_workflow", { result: { status: "invalid" } });
    expect(latestWorkflowDraft([good, bad])?.name).toBe("good");
  });
});

describe("latestWorkflowDraftId", () => {
  it("is the id of the newest drafting call, or null", () => {
    const older = tool("draft_workflow", { result: draftResult() });
    const newer = tool("revise_workflow", { result: draftResult() });
    expect(latestWorkflowDraftId([older, newer])).toBe(newer.id);
    expect(latestWorkflowDraftId([tool("read_file")])).toBeNull();
  });
});

describe("summarizeItem for the file tools", () => {
  it("reads as one human line", () => {
    expect(summarizeItem(tool("read_file", { arguments: { path: "notes/plan.md" } }))).toBe("Read plan.md");
    expect(summarizeItem(tool("write_docx", { arguments: { path: "out/report.docx" } }))).toBe("Wrote report.docx");
    expect(summarizeItem(tool("list_files", { arguments: { path: "docs/notes" } }))).toBe("Listed notes");
  });

  it("lists the workspace itself without a name", () => {
    expect(summarizeItem(tool("list_files", { arguments: { path: "." } }))).toBe("Listed files");
    expect(summarizeItem(tool("list_files"))).toBe("Listed files");
  });
});
