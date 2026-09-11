import { useEffect, useState } from "react";
import {
  createScheduledTask,
  deleteScheduledTask,
  deleteWorkflowRun,
  getScheduledTasks,
  getWorkflowRuns,
  getWorkflows,
  pauseScheduledTask,
  resumeScheduledTask,
} from "../lib/rest";
import type { ScheduledTask, ScheduleRule, Workflow, WorkflowRun, WorkflowRunStepStatus } from "../types/settings";
import { CalendarIcon, CheckCircleIcon, ClockIcon, PlusIcon, XCircleIcon, ZapIcon } from "./icons";
import type { RunTab } from "./NavRail";
import { ToggleSwitch } from "./ToggleSwitch";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const STEP_STATUS_ICON: Record<WorkflowRunStepStatus["status"], string> = {
  pending: "·",
  running: "…",
  done: "✓",
  failed: "×",
  stopped: "■",
};

function describeSchedule(schedule: ScheduleRule): string {
  switch (schedule.kind) {
    case "once":
      return `Once, ${new Date(schedule.at).toLocaleString()}`;
    case "daily":
      return `Daily at ${schedule.at}`;
    case "weekly":
      return `Weekly on ${WEEKDAYS[schedule.weekday ?? 0]} at ${schedule.at}`;
    case "monthly":
      return `Monthly on day ${schedule.day_of_month} at ${schedule.at}`;
    default:
      return schedule.kind;
  }
}

function RunStatusIcon({ status }: { status: WorkflowRun["status"] }) {
  const className = "h-4 w-4 shrink-0";
  if (status === "completed") return <CheckCircleIcon className={`${className} text-[var(--accent)]`} />;
  if (status === "running") return <ClockIcon className={`${className} text-[var(--muted)]`} />;
  return <XCircleIcon className={`${className} text-[var(--danger)]`} />;
}

interface RunPanelProps {
  runTab: RunTab;
  onRunWorkflow: (name: string) => void;
  refreshKey: number;
}

/** The Run mode's main content -- Workflows/Scheduled/History, merging
 * SessionMenu.tsx's old "Workflows"/"Recent Workflow Runs" sections with
 * settings/ScheduledTasksTab.tsx (no longer reachable from Settings, see
 * SettingsModal.tsx) into one place, since all three are "things coscribe
 * runs without you typing a message" -- see the "Nav rail, Create/Run
 * split" design pass this restructuring implements. NavRail.tsx renders a
 * compact mirror of the same data for quick access while hovered; this is
 * the full-detail view underneath it. */
export function RunPanel({ runTab, onRunWorkflow, refreshKey }: RunPanelProps) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [showTaskForm, setShowTaskForm] = useState(false);
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null);

  const [name, setName] = useState("");
  const [kind, setKind] = useState<ScheduleRule["kind"]>("daily");
  const [time, setTime] = useState("09:00");
  const [onceAt, setOnceAt] = useState("");
  const [weekday, setWeekday] = useState(0);
  const [dayOfMonth, setDayOfMonth] = useState(1);
  const [actionType, setActionType] = useState<"prompt" | "workflow">("prompt");
  const [prompt, setPrompt] = useState("");
  const [workflowName, setWorkflowName] = useState("");

  const refresh = () => {
    getWorkflows().then(setWorkflows);
    getScheduledTasks().then(setTasks);
    getWorkflowRuns().then(setRuns);
  };

  useEffect(refresh, [refreshKey]);

  const resetForm = () => {
    setName("");
    setKind("daily");
    setTime("09:00");
    setOnceAt("");
    setWeekday(0);
    setDayOfMonth(1);
    setActionType("prompt");
    setPrompt("");
    setWorkflowName("");
  };

  const submitTask = async () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setStatus({ text: "Name is required.", error: true });
      return;
    }
    if (kind === "once" && !onceAt) {
      setStatus({ text: "Pick a date and time.", error: true });
      return;
    }
    if (actionType === "prompt" && !prompt.trim()) {
      setStatus({ text: "Enter what it should do.", error: true });
      return;
    }
    if (actionType === "workflow" && !workflowName) {
      setStatus({ text: "Pick a saved workflow.", error: true });
      return;
    }
    const result = await createScheduledTask({
      name: trimmedName,
      kind,
      at: kind === "once" ? new Date(onceAt).toISOString() : time,
      ...(actionType === "prompt" ? { prompt: prompt.trim() } : { workflow_name: workflowName }),
      ...(kind === "weekly" ? { weekday } : {}),
      ...(kind === "monthly" ? { day_of_month: dayOfMonth } : {}),
    });
    if ("error" in result) {
      setStatus({ text: result.error, error: true });
      return;
    }
    setStatus({ text: "Created.", error: false });
    resetForm();
    setShowTaskForm(false);
    refresh();
  };

  const toggleTask = async (task: ScheduledTask) => {
    if (task.enabled) await pauseScheduledTask(task.trigger_id);
    else await resumeScheduledTask(task.trigger_id);
    refresh();
  };

  const removeTask = async (task: ScheduledTask) => {
    if (!window.confirm(`Delete scheduled task "${task.name}"?`)) return;
    await deleteScheduledTask(task.trigger_id);
    refresh();
  };

  const removeRun = (runId: string) => {
    if (!window.confirm("Delete this run record?")) return;
    deleteWorkflowRun(runId).then(refresh);
  };

  return (
    <div className="flex-1 overflow-y-auto p-6">
      {runTab === "workflows" && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {workflows.length === 0 && <div className="text-sm text-[var(--muted)]">No workflows saved yet.</div>}
          {workflows.map((wf) => (
            <div key={wf.name} className="rounded-lg bg-[var(--card-bg)] p-4">
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-[var(--accent)]">Workflow</div>
              <div className="mb-1 font-semibold">{wf.name}</div>
              <p className="mb-3 text-sm text-[var(--muted)]">{wf.summary}</p>
              <button
                type="button"
                className="flex items-center gap-1.5 rounded-md border border-[var(--border)] px-3 py-1.5 text-sm font-medium hover:bg-[var(--panel-bg)]"
                onClick={() => onRunWorkflow(wf.name)}
              >
                <ZapIcon className="h-3.5 w-3.5" /> Run now
              </button>
            </div>
          ))}
        </div>
      )}

      {runTab === "scheduled" && (
        <div className="flex flex-col gap-4">
          <div className="flex flex-col">
            {tasks.length === 0 && <div className="py-2 text-sm text-[var(--muted)]">No scheduled tasks yet.</div>}
            {tasks.map((task) => (
              <div key={task.trigger_id} className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-3">
                <div className="flex min-w-0 items-start gap-2">
                  <CalendarIcon className="mt-0.5 h-4 w-4 shrink-0 text-[var(--muted)]" />
                  <div className="min-w-0">
                    <div className="font-medium">{task.name}</div>
                    <div className="text-sm text-[var(--muted)]">{describeSchedule(task.schedule)}</div>
                    <div className="text-sm text-[var(--muted)]">
                      {task.workflow_name ? `Runs workflow: ${task.workflow_name}` : task.prompt}
                    </div>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  {task.next_run_at && (
                    <span className="text-sm text-[var(--muted)]">
                      Next: {new Date(task.next_run_at).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}
                    </span>
                  )}
                  <ToggleSwitch on={task.enabled} onClick={() => toggleTask(task)} />
                  <button type="button" className="text-[var(--muted)] hover:text-[var(--danger)]" onClick={() => removeTask(task)}>
                    &times;
                  </button>
                </div>
              </div>
            ))}
          </div>

          {!showTaskForm ? (
            <button
              type="button"
              className="flex items-center gap-1.5 self-start text-sm font-medium text-[var(--accent)]"
              onClick={() => setShowTaskForm(true)}
            >
              <PlusIcon className="h-3.5 w-3.5" /> New scheduled task
            </button>
          ) : (
            <div className="flex flex-col gap-2 rounded-lg bg-[var(--card-bg)] p-4">
              <input
                className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
                placeholder="Name"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <div className="flex gap-2">
                <select
                  className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm"
                  value={kind}
                  onChange={(e) => setKind(e.target.value as ScheduleRule["kind"])}
                >
                  <option value="once">Once</option>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
                {kind === "once" ? (
                  <input
                    type="datetime-local"
                    className="flex-1 rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
                    value={onceAt}
                    onChange={(e) => setOnceAt(e.target.value)}
                  />
                ) : (
                  <input
                    type="time"
                    className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
                    value={time}
                    onChange={(e) => setTime(e.target.value)}
                  />
                )}
                {kind === "weekly" && (
                  <select
                    className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm"
                    value={weekday}
                    onChange={(e) => setWeekday(Number(e.target.value))}
                  >
                    {WEEKDAYS.map((label, i) => (
                      <option key={label} value={i}>
                        {label}
                      </option>
                    ))}
                  </select>
                )}
                {kind === "monthly" && (
                  <input
                    type="number"
                    min={1}
                    max={31}
                    className="w-16 rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
                    value={dayOfMonth}
                    onChange={(e) => setDayOfMonth(Number(e.target.value))}
                  />
                )}
              </div>
              <div className="flex gap-1 border-b border-[var(--border)]">
                {(["prompt", "workflow"] as const).map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    className={`px-3 py-1.5 text-sm ${actionType === tab ? "border-b-2 border-[var(--accent)] font-medium" : "text-[var(--muted)]"}`}
                    onClick={() => setActionType(tab)}
                  >
                    {tab === "prompt" ? "Freeform instruction" : "Saved workflow"}
                  </button>
                ))}
              </div>
              {actionType === "prompt" ? (
                <textarea
                  className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
                  placeholder="What should it do each time?"
                  rows={3}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                />
              ) : (
                <select
                  className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm"
                  value={workflowName}
                  onChange={(e) => setWorkflowName(e.target.value)}
                >
                  <option value="">Select a workflow...</option>
                  {workflows.map((wf) => (
                    <option key={wf.name} value={wf.name}>
                      {wf.name}
                    </option>
                  ))}
                </select>
              )}
              <div className="flex items-center gap-3">
                <button type="button" className="self-start rounded-md bg-[var(--accent)] px-3 py-1 text-sm font-medium text-[var(--accent-fg)]" onClick={submitTask}>
                  Create
                </button>
                <button
                  type="button"
                  className="self-start text-sm text-[var(--muted)]"
                  onClick={() => {
                    setShowTaskForm(false);
                    resetForm();
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
          {status && <span className={`text-sm ${status.error ? "text-[var(--danger)]" : "text-[var(--muted)]"}`}>{status.text}</span>}
        </div>
      )}

      {runTab === "history" && (
        <div className="flex flex-col">
          {runs.length === 0 && <div className="py-2 text-sm text-[var(--muted)]">No runs yet.</div>}
          {runs.map((run) => (
            <div key={run.run_id} className="flex items-start justify-between gap-3 border-b border-[var(--border)] py-3">
              <div className="flex min-w-0 items-start gap-2">
                <RunStatusIcon status={run.status} />
                <div className="min-w-0">
                  <div className="font-medium">{run.workflow_name}</div>
                  <div className="text-sm text-[var(--muted)]">
                    {run.finished_at
                      ? new Date(run.finished_at).toLocaleString()
                      : `Started ${new Date(run.started_at).toLocaleString()}`}
                  </div>
                  {run.error && <div className="text-sm text-[var(--danger)]">{run.error}</div>}
                  {run.steps.length > 0 && (
                    <div className="mt-1 flex flex-wrap items-center gap-1">
                      {run.steps.map((step) => (
                        <span
                          key={step.index}
                          title={step.detail ?? undefined}
                          className="rounded-md border border-[var(--border)] px-1.5 py-0.5 text-xs"
                        >
                          {STEP_STATUS_ICON[step.status]} {step.tool_name}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {run.steps.length > 0 && (
                  <span className="rounded-md border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--muted)]">
                    {run.steps.length} {run.steps.length === 1 ? "step" : "steps"}
                  </span>
                )}
                {run.status !== "running" && (
                  <button
                    type="button"
                    aria-label="Delete run record"
                    className="text-[var(--muted)] hover:text-[var(--danger)]"
                    onClick={() => removeRun(run.run_id)}
                  >
                    &times;
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
