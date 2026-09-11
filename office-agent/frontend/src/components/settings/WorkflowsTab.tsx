import { useEffect, useState } from "react";
import { deleteWorkflow, deleteWorkflowRun, getWorkflowRuns, getWorkflows } from "../../lib/rest";
import type { Workflow, WorkflowRun, WorkflowRunStepStatus } from "../../types/settings";
import { ConfirmDialog } from "../ConfirmDialog";

const STEP_STATUS_ICON: Record<WorkflowRunStepStatus["status"], string> = {
  pending: "·",
  running: "…",
  done: "✓",
  failed: "×",
  stopped: "■",
};

interface WorkflowsTabProps {
  active: boolean;
  workflowEventTick: number;
}

export function WorkflowsTab({ active, workflowEventTick }: WorkflowsTabProps) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [workflowDeleteTarget, setWorkflowDeleteTarget] = useState<string | null>(null);
  const [runDeleteTarget, setRunDeleteTarget] = useState<string | null>(null);

  const refresh = () => {
    getWorkflows().then(setWorkflows);
    getWorkflowRuns().then(setRuns);
  };

  useEffect(() => {
    if (active) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, workflowEventTick]);

  const confirmRemoveWorkflow = () => {
    if (!workflowDeleteTarget) return;
    const name = workflowDeleteTarget;
    setWorkflowDeleteTarget(null);
    deleteWorkflow(name).then(refresh);
  };

  const confirmRemoveRun = () => {
    if (!runDeleteTarget) return;
    const runId = runDeleteTarget;
    setRunDeleteTarget(null);
    deleteWorkflowRun(runId).then(refresh);
  };

  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-[var(--muted)]">
        Read-only -- ask the Coordinator to change a workflow (<code>/startworkflow</code>,{" "}
        <code>/saveworkflow &lt;name&gt;</code>).
      </p>

      <div>
        <h4 className="mb-2 text-sm font-medium">Saved workflows</h4>
        {workflows.length === 0 && <div className="text-sm text-[var(--muted)]">No workflows saved yet.</div>}
        <div className="flex flex-col gap-2">
          {workflows.map((wf) => (
            <div key={wf.name} className="rounded-lg border border-[var(--border)] p-3 text-sm">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{wf.name}</span>
                  <span className="rounded-full border border-[var(--border)] px-2 py-0.5 text-xs">{wf.mode}</span>
                </div>
                <button type="button" className="text-[var(--muted)] hover:text-red-500" onClick={() => setWorkflowDeleteTarget(wf.name)}>
                  🗑
                </button>
              </div>
              <p className="mt-1 text-xs text-[var(--muted)]">{wf.summary}</p>
              {wf.last_run_at && (
                <p className="mt-1 text-xs text-[var(--muted)]">
                  Last run: {wf.last_run_status} ({new Date(wf.last_run_at).toLocaleString()})
                </p>
              )}
              {wf.mode === "chain" && wf.steps.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1">
                  {wf.steps.map((step, i) => (
                    <span key={i} className="flex items-center gap-1">
                      <span className="rounded-md border border-[var(--border)] px-2 py-1 text-xs font-mono">
                        {i + 1}. {step.tool_name}
                      </span>
                      {i < wf.steps.length - 1 && <span className="text-[var(--muted)]">→</span>}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-medium">Recent runs</h4>
        {runs.length === 0 && <div className="text-sm text-[var(--muted)]">No runs yet.</div>}
        <div className="flex flex-col gap-2">
          {runs.map((run) => (
            <div key={run.run_id} className="rounded-lg border border-[var(--border)] p-3 text-sm">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{run.workflow_name}</span>
                  <span className="rounded-full border border-[var(--border)] px-2 py-0.5 text-xs">{run.status}</span>
                </div>
                {run.status !== "running" && (
                  <button type="button" className="text-[var(--muted)] hover:text-red-500" onClick={() => setRunDeleteTarget(run.run_id)}>
                    🗑
                  </button>
                )}
              </div>
              <p className="mt-1 text-xs text-[var(--muted)]">
                {run.finished_at
                  ? `${new Date(run.started_at).toLocaleString()} -> ${new Date(run.finished_at).toLocaleString()}`
                  : `Started ${new Date(run.started_at).toLocaleString()}`}
              </p>
              {run.error && <p className="mt-1 text-xs text-red-500">{run.error}</p>}
              {run.steps.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1">
                  {run.steps.map((step) => (
                    <span key={step.index} className="rounded-md border border-[var(--border)] px-2 py-1 text-xs font-mono" title={step.detail ?? undefined}>
                      {STEP_STATUS_ICON[step.status]} {step.tool_name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {workflowDeleteTarget && (
        <ConfirmDialog
          title="Delete workflow?"
          description={`"${workflowDeleteTarget}" will be permanently removed. This does not affect any files it created.`}
          onCancel={() => setWorkflowDeleteTarget(null)}
          onConfirm={confirmRemoveWorkflow}
        />
      )}
      {runDeleteTarget && (
        <ConfirmDialog
          title="Delete run record?"
          description="This only removes the history entry -- it does not affect any files the run created."
          onCancel={() => setRunDeleteTarget(null)}
          onConfirm={confirmRemoveRun}
        />
      )}
    </div>
  );
}
