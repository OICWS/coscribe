import { useState, type ReactNode } from "react";
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
import {
  loopProgress,
  passStatus,
  recordKey,
  recordMap,
  stoppedInside,
  type LoopProgress,
} from "../../lib/workflowProgress";
import { placeSteps } from "../../lib/workflowTree";
import type { BranchStep, CheckResult, LoopStep, StepRecord, Workflow, WorkflowStep } from "../../types/workflow";
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

interface RunContext {
  workflow: Workflow;
  run: ScheduledRun;
  records: Map<string, StepRecord>;
  numbers: Map<string, number>;
  inputNames: Set<string>;
  onRetry: (stepId?: string) => Promise<string | null>;
  onAnswer: (approved: boolean, note: string) => Promise<string | null>;
  onEditStep: (stepId: string) => void;
}

interface RowProps {
  step: WorkflowStep;
  record: StepRecord | undefined;
  status: RowStatus;
  ctx: RunContext;
}

function failureLabel(step: WorkflowStep): string {
  if (step.kind === "approval") return "Not approved";
  if (step.kind === "check") return "Check failed";
  return "Failed";
}

function FailedCard({ step, record, ctx }: RowProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const number = ctx.numbers.get(step.id) ?? 0;
  const source =
    step.kind === "check"
      ? modelStepsRead(ctx.workflow, step.conditions)
          .filter((s) => (ctx.numbers.get(s.id) ?? 0) < number)
          .pop()
      : undefined;
  const sourceNumber = source ? (ctx.numbers.get(source.id) ?? null) : null;
  const declined = step.kind === "approval";
  const pass = record?.iteration?.length ? ` on item ${record.iteration[record.iteration.length - 1] + 1}` : "";

  const retry = async (stepId?: string) => {
    setBusy(true);
    setError(await ctx.onRetry(stepId));
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
          {pass}
        </span>
      </div>
      {record?.checks && record.checks.length > 0 ? (
        <CheckLines step={step} checks={record.checks} inputNames={ctx.inputNames} />
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
            <button type="button" className={secondaryButton} onClick={() => ctx.onEditStep(source.id)}>
              Edit step {sourceNumber}
            </button>
            <button type="button" className={secondaryButton} onClick={() => ctx.onEditStep(step.id)}>
              Edit this check
            </button>
          </>
        ) : (
          <>
            <button type="button" disabled={busy} className={primaryButton} onClick={() => retry(step.id)}>
              {declined ? "Ask again" : "Retry this step"}
            </button>
            <button type="button" className={secondaryButton} onClick={() => ctx.onEditStep(step.id)}>
              Edit step {number}
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

function ApprovalCard({ step, record, ctx }: RowProps) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const answer = async (approved: boolean) => {
    setBusy(true);
    setError(await ctx.onAnswer(approved, note.trim()));
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

function branchArm(record: StepRecord | undefined): "then" | "otherwise" | null {
  const arm = (record?.output as { arm?: string } | null | undefined)?.arm;
  return arm === "then" || arm === "otherwise" ? arm : null;
}

function rowSummary(step: WorkflowStep, record: StepRecord | undefined, status: RowStatus): string {
  if (status === "not_reached") return "Not reached";
  if (status === "pending") return "Up next";
  if (step.kind === "loop") {
    const progress = loopProgress(record);
    if (!progress) return status === "running" ? "Starting…" : "";
    const items = `${progress.total} ${progress.total === 1 ? "item" : "items"}`;
    if (status === "done") return `Went through ${items}`;
    if (status === "failed") return `Stopped at item ${progress.done + 1} of ${progress.total}`;
    if (status === "waiting") return `Waiting at item ${progress.done + 1} of ${progress.total}`;
    return `${progress.done} of ${items} done`;
  }
  if (status === "running") return step.kind === "llm" ? "Asking the model…" : "Running…";
  if (status === "failed") return "Stopped inside";
  if (status === "waiting") return "Waiting inside";
  if (status === "skipped") return "Skipped · its condition didn't hold";
  if (step.kind === "branch" && record?.checks?.length) {
    const first = record.checks[0];
    const arm = branchArm(record) === "then" ? "Took Then" : "Took Otherwise";
    return `${arm} · ${describeCheckResult(step.condition.op, first.held, first.left, first.right)}`;
  }
  if (step.kind === "check" && record?.checks?.length) {
    const first = record.checks[0];
    return `Passed · ${describeCheckResult(step.conditions[0].op, true, first.left, first.right)}`;
  }
  if (step.kind === "approval") return record?.note ? `Approved · “${record.note}”` : "Approved";
  return summarizeOutput(record?.output);
}

function Nested({
  label,
  color,
  note,
  children,
}: {
  label: string;
  color: string;
  note?: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={label}
      className="rounded-lg border-l-2 py-2 pl-3 pr-2"
      style={{
        borderLeftColor: `color-mix(in srgb, ${color} 55%, transparent)`,
        backgroundColor: `color-mix(in srgb, ${color} 4%, transparent)`,
      }}
    >
      <div className="mb-2 flex items-baseline gap-2 text-xs">
        <span className="font-semibold uppercase tracking-[0.08em]" style={{ color }}>
          {label}
        </span>
        {note && <span className="min-w-0 truncate text-[var(--muted)]">{note}</span>}
      </div>
      {children}
    </section>
  );
}

function stepCount(steps: WorkflowStep[]): string {
  const count = placeSteps(steps).length;
  return `${count} ${count === 1 ? "step" : "steps"}`;
}

function BranchArms({
  step,
  record,
  path,
  ctx,
}: {
  step: BranchStep;
  record: StepRecord | undefined;
  path: number[];
  ctx: RunContext;
}) {
  const taken = record?.status === "done" ? branchArm(record) : null;
  const arms = [
    { arm: "then" as const, label: "Then", steps: step.then },
    { arm: "otherwise" as const, label: "Otherwise", steps: step.otherwise },
  ];
  return (
    <div className="flex flex-col gap-2">
      {arms.map(({ arm, label, steps }) => {
        if (taken === arm) {
          return (
            <Nested key={arm} label={label} color="var(--kind-branch)">
              {steps.length > 0 ? (
                <RunList steps={steps} path={path} ctx={ctx} />
              ) : (
                <p className="text-[13px] text-[var(--muted)]">Nothing to do here.</p>
              )}
            </Nested>
          );
        }
        return (
          <p key={arm} className="flex items-baseline gap-2 pl-3.5 text-[13px] text-[var(--muted)]">
            <span className="text-xs font-semibold uppercase tracking-[0.08em]">{label}</span>
            <span>
              {stepCount(steps)} · {taken ? "not taken" : "not decided yet"}
            </span>
          </p>
        );
      })}
    </div>
  );
}

const PASS_TINT: Record<string, string> = {
  done: "var(--success)",
  failed: "var(--danger)",
  waiting: "var(--warning)",
  running: "var(--accent)",
};

function defaultPass(step: LoopStep, path: number[], progress: LoopProgress, ctx: RunContext): number {
  let latest = 0;
  for (let index = 0; index < progress.total; index++) {
    const status = passStatus(step, [...path, index], ctx.records);
    if (status === "failed" || status === "waiting") return index;
    if (status !== "not_reached") latest = index;
  }
  return latest;
}

function LoopPasses({
  step,
  record,
  path,
  ctx,
}: {
  step: LoopStep;
  record: StepRecord | undefined;
  path: number[];
  ctx: RunContext;
}) {
  const progress = loopProgress(record);
  const [chosen, setChosen] = useState<number | null>(null);
  if (!progress || progress.total === 0) {
    return (
      <p className="pl-3.5 text-[13px] text-[var(--muted)]">
        {progress ? "The list was empty, so nothing ran." : `${stepCount(step.steps)} for each ${step.item}`}
      </p>
    );
  }
  const selected = Math.min(chosen ?? defaultPass(step, path, progress, ctx), progress.total - 1);
  const label = progress.items[selected];
  return (
    <Nested
      label={`Item ${selected + 1} of ${progress.total}`}
      color="var(--kind-loop)"
      note={label ? `${step.item} = ${label}` : undefined}
    >
      <div role="tablist" aria-label={`Items of ${step.title}`} className="mb-3 flex flex-wrap gap-1">
        {Array.from({ length: progress.total }, (_, index) => {
          const status = passStatus(step, [...path, index], ctx.records);
          const tint = PASS_TINT[status];
          const active = index === selected;
          const strength = active ? 18 : 10;
          const style = tint
            ? { color: tint, backgroundColor: `color-mix(in srgb, ${tint} ${strength}%, transparent)` }
            : { color: "var(--muted)" };
          return (
            <button
              key={index}
              type="button"
              role="tab"
              aria-selected={active}
              title={progress.items[index]}
              className={`h-6 min-w-6 rounded-md px-1.5 text-xs tabular-nums transition-colors ${
                active ? "font-semibold ring-1 ring-[var(--fg)]" : "hover:bg-[var(--card-bg)]"
              }`}
              style={style}
              onClick={() => setChosen(index)}
            >
              {index + 1}
            </button>
          );
        })}
      </div>
      <RunList steps={step.steps} path={[...path, selected]} ctx={ctx} />
    </Nested>
  );
}

function StepRow({
  step,
  path,
  last,
  upNext,
  ctx,
}: {
  step: WorkflowStep;
  path: number[];
  last: boolean;
  upNext: boolean;
  ctx: RunContext;
}) {
  const record = ctx.records.get(recordKey(step.id, path));
  const status: RowStatus = record?.status ?? (upNext ? "pending" : "not_reached");
  const [open, setOpen] = useState(step.kind === "llm");
  const duration = stepDuration(record?.started_at ?? null, record?.finished_at ?? null);
  const blockStopped = stoppedInside(step, ctx.run);
  const hasOutput =
    status === "done" &&
    record?.output !== null &&
    record?.output !== undefined &&
    step.kind !== "approval" &&
    step.kind !== "branch" &&
    step.kind !== "loop";
  const muted = status === "not_reached" || status === "pending" || status === "skipped";
  const props: RowProps = { step, record, status, ctx };
  const showCard = !blockStopped && (status === "failed" || status === "waiting");

  return (
    <li className="flex gap-3.5">
      <div className="flex w-[22px] shrink-0 flex-col items-center">
        <StatusDot status={status} />
        {!last && <span className="min-h-3.5 w-px flex-1 bg-[var(--border)]" />}
      </div>
      <div className={`flex min-w-0 flex-1 flex-col gap-2.5 ${last ? "pb-1" : "pb-4"}`}>
        {showCard && status === "failed" && <FailedCard {...props} />}
        {showCard && status === "waiting" && <ApprovalCard {...props} />}
        {!showCard && (
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
        {step.kind === "branch" && <BranchArms step={step} record={record} path={path} ctx={ctx} />}
        {step.kind === "loop" && <LoopPasses step={step} record={record} path={path} ctx={ctx} />}
      </div>
    </li>
  );
}

function RunList({ steps, path, ctx }: { steps: WorkflowStep[]; path: number[]; ctx: RunContext }) {
  const firstOpen =
    ctx.run.status === "running" ? steps.findIndex((step) => !ctx.records.has(recordKey(step.id, path))) : -1;
  const previousDone = firstOpen > 0 && ctx.records.get(recordKey(steps[firstOpen - 1].id, path))?.status !== "running";
  return (
    <ol className="flex flex-col">
      {steps.map((step, index) => (
        <StepRow
          key={step.id}
          step={step}
          path={path}
          last={index === steps.length - 1}
          upNext={index === firstOpen && (index === 0 ? path.length === 0 : previousDone)}
          ctx={ctx}
        />
      ))}
    </ol>
  );
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
 * where it stopped and why, and the way on from there. A branch shows the
 * arm it took; a loop shows one item's pass at a time. */
export function WorkflowRunView({ task, run, workflow, onRetry, onAnswer, onEditStep }: WorkflowRunViewProps) {
  const duration = runDuration(run);
  const crashed = run.status === "failed" && !run.steps.some((record) => record.status === "failed");
  const [retrying, setRetrying] = useState(false);
  const [crashError, setCrashError] = useState<string | null>(null);
  const ctx: RunContext = {
    workflow,
    run,
    records: recordMap(run),
    numbers: new Map(placeSteps(workflow.steps).map((placed) => [placed.step.id, placed.number])),
    inputNames: new Set(workflow.inputs.map((input) => input.name)),
    onRetry,
    onAnswer,
    onEditStep,
  };

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

        <RunList steps={workflow.steps} path={[]} ctx={ctx} />
      </div>
    </div>
  );
}
