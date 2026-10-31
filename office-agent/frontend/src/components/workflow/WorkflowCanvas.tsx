import { Fragment, useMemo, type ReactNode } from "react";
import { AUTONOMY_LABEL, KIND_COLOR, KIND_LABEL } from "../../lib/workflowLabels";
import { type NodeRunState, runMap } from "../../lib/workflowRunMap";
import { placeSteps } from "../../lib/workflowTree";
import type { ScheduledRun } from "../../types/settings";
import type { BranchStep, LoopStep, Workflow, WorkflowStep } from "../../types/workflow";
import { LoopIcon } from "../icons";
import { StepKindTile, VarToken } from "./parts";
import { StatusDot } from "./StatusDot";
import { StepSummary } from "./StepSummary";

// The diagram is laid out by the workflow's own structure (a list runs top
// to bottom, a branch's arms side by side, a loop's body inside a frame), so
// it needs no graph layout engine and can't come out in an order the engine
// wouldn't run.

interface CanvasContext {
  numbers: Map<string, number>;
  inputNames: Set<string>;
  /** Each step's state when the canvas shows a run. */
  states?: Map<string, NodeRunState>;
  onOpenStep?: (stepId: string) => void;
}

const LINE = "bg-[var(--border-hover)]";

function Arrow() {
  return (
    <div aria-hidden="true" className="flex flex-col items-center">
      <div className={`h-4 w-px ${LINE}`} />
      <svg width="9" height="6" viewBox="0 0 9 6" className="text-[var(--border-hover)]">
        <path d="M0 0h9L4.5 6z" fill="currentColor" />
      </svg>
    </div>
  );
}

function Terminal({ faded = false, children }: { faded?: boolean; children: ReactNode }) {
  return (
    <div
      className={`${faded ? "opacity-55 " : ""}flex max-w-80 flex-wrap items-baseline justify-center gap-x-1.5 gap-y-1 rounded-full border border-[var(--border)] bg-[var(--bg)] px-3.5 py-1.5 text-xs text-[var(--muted)] shadow-sm`}
    >
      {children}
    </div>
  );
}

const RUN_TEXT: Partial<Record<NodeRunState["status"], string>> = {
  running: "Running…",
  waiting: "Waiting for you",
  failed: "Failed here",
  stopped: "Stopped here",
  skipped: "Skipped",
  not_taken: "Not taken",
  pending: "Up next",
};

function loopText(state: NodeRunState, done: number, total: number | null): string {
  const of = total === null ? "" : ` of ${total}`;
  if (state.status === "failed" || state.status === "stopped") return `Stopped at item ${done + 1}${of}`;
  if (state.status === "waiting") return `Waiting at item ${done + 1}${of}`;
  const through = `${done}${of} ${(total ?? done) === 1 ? "item" : "items"} through`;
  return state.status === "running" ? `Running… · ${through}` : through;
}

function runText(step: WorkflowStep, state: NodeRunState): string | null {
  const { passes } = state;
  const lead = RUN_TEXT[state.status] ?? null;
  if (passes && (state.status !== "not_reached" || passes.done > 0)) {
    if (step.kind === "loop") return loopText(state, passes.done, passes.total);
    // A step inside a loop: how many items it has been done for so far.
    const count = passes.total === null ? `${passes.done}` : `${passes.done} of ${passes.total}`;
    const items = `${count} ${(passes.total ?? passes.done) === 1 ? "item" : "items"}`;
    if (!lead || state.status === "skipped") return `Done for ${items}`;
    return passes.done > 0 ? `${lead} · done for ${items}` : lead;
  }
  if (step.kind === "branch" && state.arms?.length) {
    return `Took ${state.arms.map((arm) => (arm === "then" ? "Then" : "Otherwise")).join(" and ")}`;
  }
  return lead;
}

// How a node looks for its state: a ring where the run needs attention or
// is working, faded where it hasn't been or won't be.
function runLook(state: NodeRunState | undefined): { className: string; ring?: string } {
  if (!state) return { className: "" };
  if (state.status === "failed") return { className: "", ring: "var(--danger)" };
  if (state.status === "waiting") return { className: "", ring: "var(--warning)" };
  if (state.status === "running") return { className: "", ring: "var(--accent)" };
  if (state.status === "not_reached" || state.status === "not_taken" || state.status === "skipped") {
    return { className: "opacity-55" };
  }
  return { className: "" };
}

function StepNode({ step, ctx }: { step: WorkflowStep; ctx: CanvasContext }) {
  const color = KIND_COLOR[step.kind];
  const state = ctx.states?.get(step.id);
  const note = state ? runText(step, state) : null;
  const look = runLook(state);
  const kind = step.kind === "agent" ? `Agent · ${AUTONOMY_LABEL[step.autonomy]}` : KIND_LABEL[step.kind];
  const body = (
    <>
      <span className="flex items-center gap-2">
        <StepKindTile step={step} size="sm" />
        <span className="text-[11px] font-medium uppercase tracking-[0.06em]" style={{ color }}>
          {kind}
        </span>
        <span className="ml-auto text-xs tabular-nums text-[var(--muted)]">{ctx.numbers.get(step.id)}</span>
        {state && <StatusDot status={state.status} />}
      </span>
      <span className="mt-1.5 line-clamp-2 block text-[13px] font-medium leading-snug" title={step.title}>
        {step.title}
      </span>
      <span className="mt-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-1 text-xs text-[var(--muted)]">
        <StepSummary step={step} inputNames={ctx.inputNames} />
      </span>
      {note && (
        <span
          className="mt-1.5 block border-t border-[var(--border)] pt-1.5 text-xs font-medium"
          style={{ color: look.ring }}
        >
          {note}
        </span>
      )}
    </>
  );
  const className = `block w-64 rounded-xl border border-[var(--border)] bg-[var(--bg)] px-3 pb-2.5 pt-2 text-left shadow-sm ${look.className}`;
  const style = {
    borderTopColor: color,
    borderTopWidth: 3,
    ...(look.ring ? { boxShadow: `0 0 0 2px color-mix(in srgb, ${look.ring} 45%, transparent)` } : {}),
  };
  if (!ctx.onOpenStep) {
    return (
      <div className={className} style={style} data-canvas-step={step.id} data-run-status={state?.status}>
        {body}
      </div>
    );
  }
  return (
    <button
      type="button"
      className={`${className} transition-colors hover:border-[var(--border-hover)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]`}
      style={style}
      data-canvas-step={step.id}
      data-run-status={state?.status}
      title={ctx.states ? `Show "${step.title}" in the run` : `Edit "${step.title}"`}
      onClick={() => ctx.onOpenStep?.(step.id)}
    >
      {body}
    </button>
  );
}

function ArmLabel({ taken, children }: { taken: boolean; children: ReactNode }) {
  const color = KIND_COLOR.branch;
  return (
    <span
      className={`rounded-full border px-2 py-px text-[11px] ${taken ? "font-medium" : "border-[var(--border)] bg-[var(--card-bg)] text-[var(--muted)]"}`}
      style={
        taken
          ? { color, borderColor: color, backgroundColor: `color-mix(in srgb, ${color} 10%, transparent)` }
          : undefined
      }
    >
      {children}
    </span>
  );
}

function BranchBlock({ step, ctx }: { step: BranchStep; ctx: CanvasContext }) {
  const arms: [string, string, WorkflowStep[]][] = [
    ["then", "Then", step.then],
    ["otherwise", "Otherwise", step.otherwise],
  ];
  const taken = ctx.states?.get(step.id)?.arms ?? [];
  return (
    <div className="flex flex-col items-center">
      <StepNode step={step} ctx={ctx} />
      <div aria-hidden="true" className={`h-4 w-px ${LINE}`} />
      <div className="flex">
        {arms.map(([arm, label, steps], index) => (
          <div key={label} className="relative flex flex-col items-center px-4">
            {/* Each arm draws the half of the split and join lines on its own
                side, so they meet whatever width each arm needs. */}
            <div
              aria-hidden="true"
              className={`absolute top-0 h-px ${index === 0 ? "left-1/2 right-0" : "left-0 right-1/2"} ${LINE}`}
            />
            <div
              aria-hidden="true"
              className={`absolute bottom-0 h-px ${index === 0 ? "left-1/2 right-0" : "left-0 right-1/2"} ${LINE}`}
            />
            <div aria-hidden="true" className={`h-3 w-px ${LINE}`} />
            <ArmLabel taken={taken.includes(arm)}>{label}</ArmLabel>
            {steps.length > 0 ? (
              <>
                <Arrow />
                <Flow steps={steps} ctx={ctx} />
              </>
            ) : (
              <span className="mt-2 text-xs italic text-[var(--muted)]">nothing</span>
            )}
            <div aria-hidden="true" className={`min-h-4 w-px flex-1 ${LINE}`} />
          </div>
        ))}
      </div>
    </div>
  );
}

function LoopBlock({ step, ctx }: { step: LoopStep; ctx: CanvasContext }) {
  const color = KIND_COLOR.loop;
  return (
    <div
      className="flex flex-col items-center rounded-2xl border border-dashed px-5 pb-5 pt-3"
      style={{
        borderColor: `color-mix(in srgb, ${color} 55%, transparent)`,
        backgroundColor: `color-mix(in srgb, ${color} 4%, transparent)`,
      }}
    >
      <div className="mb-3 flex w-full items-center gap-1.5 text-xs" style={{ color }}>
        <LoopIcon className="h-3.5 w-3.5" />
        <span className="font-medium">Repeats for each item</span>
        <span className="ml-auto text-[var(--muted)]">at most {step.max_items}</span>
      </div>
      <StepNode step={step} ctx={ctx} />
      {step.steps.length > 0 ? (
        <>
          <Arrow />
          <Flow steps={step.steps} ctx={ctx} />
        </>
      ) : (
        <span className="mt-3 text-xs italic text-[var(--muted)]">no steps inside yet</span>
      )}
    </div>
  );
}

function Flow({ steps, ctx }: { steps: WorkflowStep[]; ctx: CanvasContext }) {
  return (
    <div className="flex flex-col items-center">
      {steps.map((step, index) => (
        <Fragment key={step.id}>
          {index > 0 && <Arrow />}
          {step.kind === "branch" && <BranchBlock step={step} ctx={ctx} />}
          {step.kind === "loop" && <LoopBlock step={step} ctx={ctx} />}
          {step.kind !== "branch" && step.kind !== "loop" && <StepNode step={step} ctx={ctx} />}
        </Fragment>
      ))}
    </div>
  );
}

/** A read-only picture of a workflow: what runs, in what order, which way
 * a branch can go and what repeats. Clicking a step opens it for editing
 * when `onOpenStep` is given. */
export function WorkflowCanvas({
  workflow,
  run,
  onOpenStep,
}: {
  workflow: Workflow;
  /** Draws how this run went on the workflow. */
  run?: ScheduledRun;
  onOpenStep?: (stepId: string) => void;
}) {
  const ctx = useMemo<CanvasContext>(
    () => ({
      numbers: new Map(placeSteps(workflow.steps).map((placed) => [placed.step.id, placed.number])),
      inputNames: new Set(workflow.inputs.map((input) => input.name)),
      states: run ? runMap(workflow, run) : undefined,
      onOpenStep,
    }),
    [workflow, run, onOpenStep],
  );
  return (
    <div
      data-testid="workflow-canvas"
      className="overflow-x-auto rounded-xl border border-[var(--border)] bg-[var(--card-bg)]"
      style={{
        backgroundImage: "radial-gradient(color-mix(in srgb, var(--muted) 30%, transparent) 1px, transparent 1px)",
        backgroundSize: "16px 16px",
      }}
    >
      <div className="mx-auto flex w-max min-w-full flex-col items-center px-8 py-7">
        <Terminal>
          <span className="font-medium text-[var(--fg)]">Start</span>
          {workflow.inputs.length > 0 && <span>with</span>}
          {workflow.inputs.map((input) => (
            <VarToken key={input.name} name={input.name} isInput />
          ))}
        </Terminal>
        <Arrow />
        {workflow.steps.length > 0 ? (
          <Flow steps={workflow.steps} ctx={ctx} />
        ) : (
          <span className="text-xs italic text-[var(--muted)]">No steps yet</span>
        )}
        <Arrow />
        <Terminal faded={run !== undefined && run.status !== "completed"}>
          <span className="font-medium text-[var(--fg)]">Done</span>
        </Terminal>
      </div>
    </div>
  );
}
