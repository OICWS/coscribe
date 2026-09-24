import { useState } from "react";
import { capitalize, formatRunTime, runDuration, runSourceLabel } from "../../lib/runLabels";
import {
  describeCheckResult,
  formatValue,
  modelStepsRead,
  stepConditions,
  stepDuration,
  summarizeOutput,
} from "../../lib/workflowLabels";
import type { ScheduledRun, ScheduledTask } from "../../types/settings";
import type { CheckResult, StepRecord, Workflow, WorkflowStep } from "../../types/workflow";
import { CheckIcon, ChevronDownIcon, ClockIcon, CloseIcon } from "../icons";
import { ConditionText, VarToken } from "./parts";
import { StepOutput } from "./StepOutput";

type RowStatus = StepRecord["status"] | "not_reached";

const primaryButton =
  "h-8 rounded-md bg-[var(--primary)] px-3 text-[13px] font-medium text-[var(--primary-fg)] hover:bg-[var(--primary-hover)] disabled:opacity-50";
const secondaryButton =
  "h-8 rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 text-[13px] hover:bg-[var(--card-bg)] disabled:opacity-50";

function StatusDot({ status }: { status: RowStatus }) {
  const base = "flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full";
  const tint = (color: string) => ({ color, backgroundColor: `color-mix(in srgb, ${color} 16%, transparent)` });
  if (status === "done") {
    return (
      <span className={base} style={tint("var(--success)")} role="img" aria-label="Done">
        <CheckIcon className="h-3 w-3" strokeWidth={3} />
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className={base} style={tint("var(--danger)")} role="img" aria-label="Failed">
        <CloseIcon className="h-3 w-3" strokeWidth={3} />
      </span>
    );
  }
  if (status === "waiting") {
    return (
      <span className={base} style={tint("var(--warning)")} role="img" aria-label="Waiting for you">
        <ClockIcon className="h-3 w-3" strokeWidth={2.5} />
      </span>
    );
  }
  if (status === "running") {
    return (
      <span className={`${base} border border-[var(--border-hover)]`} role="img" aria-label="Running">
        <span className="h-3 w-3 animate-spin rounded-full border-[1.5px] border-[var(--border)] border-t-[var(--accent)] motion-reduce:animate-none" />
      </span>
    );
  }
  if (status === "skipped") {
    return (
      <span className={`${base} bg-[var(--card-bg)] text-[var(--muted)]`} role="img" aria-label="Skipped">
        <span className="h-[1.5px] w-2 rounded bg-current" />
      </span>
    );
  }
  return (
    <span
      className={`${base} border-[1.5px] border-dashed border-[var(--border-hover)]`}
      role="img"
      aria-label={status === "pending" ? "Up next" : "Not reached"}
    />
  );
}

function CheckLines({
  step,
  checks,
  inputNames,
}: {
  step: WorkflowStep;
  checks: CheckResult[];
  inputNames: Set<string>;
}) {
  const conditions = stepConditions(step);
  return (
    <ul className="flex flex-col gap-1.5 text-[13.5px]">
      {checks.map((check, index) => (
        <li key={index} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span
            aria-label={check.held ? "Held" : "Failed"}
            className="w-3 shrink-0 font-semibold"
            style={{ color: check.held ? "var(--success)" : "var(--danger)" }}
          >
            {check.held ? "✓" : "✕"}
          </span>
          {conditions[index] && <ConditionText condition={conditions[index]} inputNames={inputNames} />}
          {conditions[index] && (
            <span
              className={`tabular-nums ${check.held ? "text-[var(--muted)]" : "font-medium"}`}
              style={check.held ? undefined : { color: "var(--danger)" }}
            >
              {describeCheckResult(conditions[index].op, check.held, check.left, check.right)}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

interface RowProps {
  step: WorkflowStep;
  index: number;
  record: StepRecord | undefined;
  status: RowStatus;
  last: boolean;
  workflow: Workflow;
  inputNames: Set<string>;
  onRetry: (stepId?: string) => Promise<string | null>;
  onAnswer: (approved: boolean, note: string) => Promise<string | null>;
  onEditStep: (stepId: string) => void;
}

function failureLabel(step: WorkflowStep): string {
  if (step.kind === "approval") return "Not approved";
  if (step.kind === "check") return "Check failed";
  return "Failed";
}

function FailedCard({ step, index, record, workflow, inputNames, onRetry, onEditStep }: RowProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const source =
    step.kind === "check"
      ? modelStepsRead(workflow, step.conditions)
          .filter((s) => workflow.steps.indexOf(s) < index)
          .pop()
      : undefined;
  const sourceNumber = source ? workflow.steps.indexOf(source) + 1 : null;
  const declined = step.kind === "approval";

  const retry = async (stepId?: string) => {
    setBusy(true);
    setError(await onRetry(stepId));
    setBusy(false);
  };

  return (
    <div
      className="flex flex-col gap-3 rounded-xl border px-4 py-3.5"
      style={{
        borderColor: "color-mix(in srgb, var(--danger) 35%, transparent)",
        backgroundColor: "color-mix(in srgb, var(--danger) 6%, var(--bg))",
      }}
    >
      <div className="flex flex-wrap items-baseline gap-x-2.5">
        <span className="text-sm font-medium">{step.title}</span>
        <span className="text-[13px] font-medium" style={{ color: "var(--danger)" }}>
          {failureLabel(step)}
        </span>
      </div>
      {record?.checks && record.checks.length > 0 ? (
        <CheckLines step={step} checks={record.checks} inputNames={inputNames} />
      ) : (
        record?.error && (
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[12px] leading-relaxed text-[var(--fg)]">
            {record.error}
          </pre>
        )
      )}
      <p className="text-[13px] text-[var(--muted)]">
        {source
          ? `Those values came from step ${sourceNumber}, "${source.title}". Nothing after this check ran.`
          : "Nothing after this step ran."}
      </p>
      <div className="flex flex-wrap gap-2">
        {source && sourceNumber !== null ? (
          <>
            <button type="button" disabled={busy} className={primaryButton} onClick={() => retry(source.id)}>
              Retry from step {sourceNumber}
            </button>
            <button type="button" className={secondaryButton} onClick={() => onEditStep(source.id)}>
              Edit step {sourceNumber}
            </button>
          </>
        ) : (
          <>
            <button type="button" disabled={busy} className={primaryButton} onClick={() => retry(step.id)}>
              {declined ? "Ask again" : "Retry this step"}
            </button>
            <button type="button" className={secondaryButton} onClick={() => onEditStep(step.id)}>
              Edit step {index + 1}
            </button>
          </>
        )}
      </div>
      {error && (
        <p role="alert" className="text-[13px] text-[var(--danger)]">
          {error}
        </p>
      )}
    </div>
  );
}

function ApprovalCard({ step, record, onAnswer }: RowProps) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const answer = async (approved: boolean) => {
    setBusy(true);
    setError(await onAnswer(approved, note.trim()));
    setBusy(false);
  };
  return (
    <div
      className="flex flex-col gap-3 rounded-xl border px-4 py-3.5"
      style={{
        borderColor: "color-mix(in srgb, var(--warning) 40%, transparent)",
        backgroundColor: "color-mix(in srgb, var(--warning) 7%, var(--bg))",
      }}
    >
      <div className="flex flex-wrap items-baseline gap-x-2.5">
        <span className="text-sm font-medium">{step.title}</span>
        <span className="text-[13px] font-medium" style={{ color: "var(--warning)" }}>
          Waiting for you
        </span>
      </div>
      {typeof record?.output === "string" && (
        <p className="whitespace-pre-wrap text-sm leading-relaxed">{record.output}</p>
      )}
      <label className="flex flex-col gap-1.5">
        <span className="text-xs text-[var(--muted)]">Note (optional, saved with this run)</span>
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          className="rounded-md border border-[var(--border)] bg-[var(--bg)] px-2.5 py-1.5 text-sm outline-none hover:border-[var(--border-hover)] focus:border-[var(--border-hover)]"
        />
      </label>
      <div className="flex gap-2">
        <button type="button" disabled={busy} className={primaryButton} onClick={() => answer(true)}>
          Approve and continue
        </button>
        <button type="button" disabled={busy} className={secondaryButton} onClick={() => answer(false)}>
          Decline
        </button>
      </div>
      {error && (
        <p role="alert" className="text-[13px] text-[var(--danger)]">
          {error}
        </p>
      )}
    </div>
  );
}

function rowSummary(step: WorkflowStep, record: StepRecord | undefined, status: RowStatus): string {
  if (status === "not_reached") return "Not reached";
  if (status === "pending") return "Up next";
  if (status === "running") return step.kind === "llm" ? "Asking the model…" : "Running…";
  if (status === "skipped") return "Skipped · its condition didn't hold";
  if (step.kind === "check" && record?.checks?.length) {
    const first = record.checks[0];
    return `Passed · ${describeCheckResult(step.conditions[0].op, true, first.left, first.right)}`;
  }
  if (step.kind === "approval") return record?.note ? `Approved · “${record.note}”` : "Approved";
  return summarizeOutput(record?.output);
}

function StepRow(props: RowProps) {
  const { step, record, status, last } = props;
  const [open, setOpen] = useState(step.kind === "llm");
  const duration = stepDuration(record?.started_at ?? null, record?.finished_at ?? null);
  const hasOutput =
    status === "done" && record?.output !== null && record?.output !== undefined && step.kind !== "approval";
  const muted = status === "not_reached" || status === "pending" || status === "skipped";

  return (
    <li className="flex gap-3.5">
      <div className="flex w-[22px] shrink-0 flex-col items-center">
        <StatusDot status={status} />
        {!last && <span className="min-h-3.5 w-px flex-1 bg-[var(--border)]" />}
      </div>
      <div className={`flex min-w-0 flex-1 flex-col gap-2.5 ${last ? "pb-1" : "pb-4"}`}>
        {status === "failed" && <FailedCard {...props} />}
        {status === "waiting" && <ApprovalCard {...props} />}
        {status !== "failed" && status !== "waiting" && (
          <>
            <div className="flex min-w-0 items-baseline gap-2.5">
              <span className={`shrink-0 text-sm ${muted ? "text-[var(--muted)]" : "font-medium"}`}>{step.title}</span>
              {step.kind === "llm" && status === "done" && (
                <span className="shrink-0 rounded-full bg-[color-mix(in_srgb,var(--kind-llm)_13%,transparent)] px-2 py-px text-xs text-[var(--kind-llm)]">
                  Model
                </span>
              )}
              <span className="min-w-0 truncate text-[13px] text-[var(--muted)]">
                {rowSummary(step, record, status)}
              </span>
              <span className="flex-1" />
              {duration && <span className="shrink-0 text-xs tabular-nums text-[var(--muted)]">{duration}</span>}
              {hasOutput && (
                <button
                  type="button"
                  aria-expanded={open}
                  aria-label={open ? `Hide output of ${step.title}` : `Show output of ${step.title}`}
                  className="flex h-6 w-6 shrink-0 items-center justify-center self-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
                  onClick={() => setOpen((v) => !v)}
                >
                  <ChevronDownIcon
                    className={`h-3.5 w-3.5 transition-transform motion-reduce:transition-none ${open ? "rotate-180" : ""}`}
                  />
                </button>
              )}
            </div>
            {hasOutput && open && <StepOutput value={record?.output} />}
          </>
        )}
      </div>
    </li>
  );
}

function rowStatus(record: StepRecord | undefined, run: ScheduledRun, index: number, firstOpen: number): RowStatus {
  if (record) return record.status;
  if (run.status === "running") return index === firstOpen ? "pending" : "not_reached";
  return "not_reached";
}

interface WorkflowRunViewProps {
  task: ScheduledTask;
  run: ScheduledRun;
  workflow: Workflow;
  onRetry: (stepId?: string) => Promise<string | null>;
  onAnswer: (approved: boolean, note: string) => Promise<string | null>;
  onEditStep: (stepId: string) => void;
}

/** One workflow run, step by step (the Run artboard of
 * https://claude.ai/artifact/4G5JyZ3r4tMPF6QcFG3Vj1): what each step did,
 * where it stopped and why, and the way on from there. */
export function WorkflowRunView({ task, run, workflow, onRetry, onAnswer, onEditStep }: WorkflowRunViewProps) {
  const inputNames = new Set(workflow.inputs.map((input) => input.name));
  const records = new Map(run.steps.map((record) => [record.step_id, record]));
  const firstOpen = workflow.steps.findIndex((step) => !records.has(step.id));
  const duration = runDuration(run);
  const crashed = run.status === "failed" && !run.steps.some((record) => record.status === "failed");
  const [retrying, setRetrying] = useState(false);
  const [crashError, setCrashError] = useState<string | null>(null);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[760px] flex-col gap-6 px-4 pb-16 pt-5">
        <div className="flex flex-col gap-2">
          <div className="text-[13px] text-[var(--muted)]">
            Run · {capitalize(formatRunTime(run.started_at))} · {runSourceLabel(run)}
            {duration && ` · ${duration}`}
            <span className="sr-only"> of {task.name}</span>
          </div>
          {workflow.inputs.length > 0 && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm">
              {workflow.inputs.map((input) => (
                <span key={input.name} className="inline-flex items-center gap-2">
                  <VarToken name={input.name} isInput />
                  <span className="rounded-md bg-[var(--card-bg)] px-2 py-0.5 text-[13px]">
                    {formatValue(run.inputs?.[input.name] ?? input.default)}
                  </span>
                </span>
              ))}
            </div>
          )}
        </div>

        {crashed && (
          <div className="flex flex-wrap items-center gap-3 rounded-xl border border-[var(--border)] px-4 py-3 text-sm">
            <span className="min-w-0 flex-1">{run.error ?? "This run stopped before it finished."}</span>
            <button
              type="button"
              disabled={retrying}
              className={primaryButton}
              onClick={async () => {
                setRetrying(true);
                setCrashError(await onRetry());
                setRetrying(false);
              }}
            >
              Continue the run
            </button>
            {crashError && <p className="w-full text-[13px] text-[var(--danger)]">{crashError}</p>}
          </div>
        )}

        <ol className="flex flex-col">
          {workflow.steps.map((step, index) => (
            <StepRow
              key={step.id}
              step={step}
              index={index}
              record={records.get(step.id)}
              status={rowStatus(records.get(step.id), run, index, firstOpen)}
              last={index === workflow.steps.length - 1}
              workflow={workflow}
              inputNames={inputNames}
              onRetry={onRetry}
              onAnswer={onAnswer}
              onEditStep={onEditStep}
            />
          ))}
        </ol>
      </div>
    </div>
  );
}
