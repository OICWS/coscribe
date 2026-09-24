import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { inputClass, primaryButton, secondaryButton } from "../../lib/formStyles";
import { getTools } from "../../lib/rest";
import { moveStep, newStep, renameValue, suggestionsBefore } from "../../lib/workflowEdit";
import { stepOutput } from "../../lib/workflowLabels";
import type { ToolInfo } from "../../types/settings";
import type { StepKind, Workflow, WorkflowInput, WorkflowStep } from "../../types/workflow";
import { ConfirmDialog } from "../ConfirmDialog";
import { ArrowDownIcon, ArrowUpIcon, ChevronDownIcon, PlusIcon, TrashIcon } from "../icons";
import { AddStepMenu } from "./AddStepMenu";
import { FieldLabel } from "./fields";
import { InputsEditor } from "./InputsEditor";
import { KindLegend, SectionHeading, StepKindTile } from "./parts";
import { StepFields } from "./StepEditors";
import { StepSummary } from "./StepSummary";

/** Saves (or, for a draft, only checks) the whole workflow; resolves to
 * the server's refusal, or null once it holds. */
export type Persist = (workflow: Workflow) => Promise<string | null>;

/** The server's validation message, led by what the edit was. */
function refusal(action: string, problem: string | null): string | null {
  if (!problem) return null;
  return `${action} -- ${problem.replace(/^The workflow isn't valid: /, "")}`;
}

interface StepCardProps {
  step: WorkflowStep;
  index: number;
  total: number;
  isNew: boolean;
  expanded: boolean;
  workflow: Workflow;
  tools: Map<string, ToolInfo>;
  inputNames: Set<string>;
  onToggle: () => void;
  onSave: (step: WorkflowStep) => Promise<string | null>;
  onMove: (delta: -1 | 1) => Promise<string | null>;
  onDelete: () => void;
  onCancelNew: () => void;
}

function StepCard({
  step,
  index,
  total,
  isNew,
  expanded,
  workflow,
  tools,
  inputNames,
  onToggle,
  onSave,
  onMove,
  onDelete,
  onCancelNew,
}: StepCardProps) {
  const [draft, setDraft] = useState(step);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!expanded) {
      setDraft(step);
      setError(null);
    }
  }, [expanded, step]);

  useEffect(() => {
    if (isNew) titleRef.current?.select();
  }, [isNew]);

  const dirty = isNew || JSON.stringify(draft) !== JSON.stringify(step);
  let saveLabel = isNew ? "Add step" : "Save step";
  if (busy) saveLabel = "Saving…";
  const context = useMemo(
    () => ({
      suggestions: suggestionsBefore(workflow, index),
      inputNames,
      tools,
      listId: `values-${step.id}`,
    }),
    [workflow, index, inputNames, tools, step.id],
  );

  const run = async (action: () => Promise<string | null>) => {
    setBusy(true);
    const problem = await action();
    setBusy(false);
    setError(problem);
    return problem;
  };

  const save = async () => {
    const problem = await run(() => onSave(draft));
    if (!problem && !isNew) onToggle();
  };

  const iconButton =
    "flex h-7 w-7 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)] disabled:pointer-events-none disabled:opacity-30";

  return (
    <li
      data-step={step.id}
      className={`relative rounded-xl border bg-[var(--bg)] transition-shadow ${
        expanded
          ? "border-[color-mix(in_srgb,var(--accent)_70%,transparent)] shadow-[0_6px_24px_rgba(0,0,0,0.07)]"
          : "border-[var(--border)] hover:border-[var(--border-hover)]"
      }`}
    >
      <div className="flex items-center gap-1 pr-2">
        <button
          type="button"
          aria-expanded={expanded}
          disabled={isNew}
          className="flex min-w-0 flex-1 items-center gap-3.5 rounded-xl py-3 pl-3.5 pr-1.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-soft)] disabled:cursor-default"
          onClick={onToggle}
        >
          <StepKindTile step={step} />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="truncate text-sm font-medium">{isNew ? draft.title || "New step" : step.title}</span>
            {!expanded && (
              <span className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 gap-y-1 text-[13px] text-[var(--muted)]">
                <StepSummary step={step} inputNames={inputNames} />
              </span>
            )}
          </span>
          {expanded && step.kind === "llm" && (
            <span className="hidden shrink-0 text-xs text-[var(--muted)] sm:inline">
              {step.model ?? "Task's model"} · temperature 0
            </span>
          )}
          <span className="w-5 shrink-0 text-right text-xs tabular-nums text-[var(--muted)]">{index + 1}</span>
          {!isNew && (
            <ChevronDownIcon
              className={`h-4 w-4 shrink-0 text-[var(--muted)] transition-transform motion-reduce:transition-none ${expanded ? "rotate-180" : ""}`}
            />
          )}
        </button>
        {expanded && !isNew && (
          <span className="flex shrink-0 items-center border-l border-[var(--border)] pl-1">
            <button
              type="button"
              aria-label="Move step up"
              title="Move up"
              disabled={busy || index === 0}
              className={iconButton}
              onClick={() => run(() => onMove(-1))}
            >
              <ArrowUpIcon className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              aria-label="Move step down"
              title="Move down"
              disabled={busy || index === total - 1}
              className={iconButton}
              onClick={() => run(() => onMove(1))}
            >
              <ArrowDownIcon className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              aria-label="Delete step"
              title="Delete"
              disabled={busy || total === 1}
              className={`${iconButton} hover:text-[var(--danger)]`}
              onClick={onDelete}
            >
              <TrashIcon className="h-3.5 w-3.5" />
            </button>
          </span>
        )}
      </div>
      {expanded && (
        <div className="flex flex-col gap-4 px-4 pb-4 pl-[58px]">
          <div className="max-w-md">
            <FieldLabel htmlFor={`${step.id}-title`}>Step name</FieldLabel>
            <input
              ref={titleRef}
              id={`${step.id}-title`}
              className={inputClass}
              value={draft.title}
              maxLength={120}
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            />
          </div>
          <StepFields step={draft} onChange={setDraft} context={context} />
          {error && (
            <p role="alert" className="text-sm text-[var(--danger)]">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" className={secondaryButton} onClick={isNew ? onCancelNew : onToggle}>
              {isNew || dirty ? "Discard" : "Close"}
            </button>
            <button
              type="button"
              disabled={!dirty || busy || !draft.title.trim()}
              className={primaryButton}
              onClick={save}
            >
              {saveLabel}
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

function InsertPoint({
  label,
  open,
  onOpen,
  children,
}: {
  label: string;
  open: boolean;
  onOpen: () => void;
  children: ReactNode;
}) {
  return (
    <li className="group relative h-2">
      <span className="absolute inset-x-0 -inset-y-1.5" aria-hidden="true" />
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        title={label}
        className={`absolute left-[18px] top-1/2 z-10 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded-full border border-[var(--border-hover)] bg-[var(--bg)] text-[var(--muted)] transition-opacity hover:text-[var(--fg)] focus-visible:opacity-100 group-hover:opacity-100 motion-reduce:transition-none ${
          open ? "opacity-100" : "opacity-0"
        }`}
        onClick={onOpen}
      >
        <PlusIcon className="h-3 w-3" />
      </button>
      <div className="absolute left-[18px] top-1/2">{children}</div>
    </li>
  );
}

/** A workflow's inputs and steps (the Edit artboard of
 * https://claude.ai/artifact/4G5JyZ3r4tMPF6QcFG3Vj1). One step open at a
 * time; every change goes through `persist` with the whole workflow, which
 * the server re-validates -- so a refusal (a later step reading a deleted
 * result, a move past a value's source) comes back to the step that
 * caused it. */
export function WorkflowEditor({
  workflow,
  persist,
  focusStepId,
}: {
  workflow: Workflow;
  persist: Persist;
  focusStepId?: string | null;
}) {
  const [expandedId, setExpandedId] = useState<string | null>(focusStepId ?? null);
  const [pending, setPending] = useState<{ index: number; step: WorkflowStep } | null>(null);
  const [menuAt, setMenuAt] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<WorkflowStep | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const listRef = useRef<HTMLOListElement>(null);
  const inputNames = useMemo(() => new Set(workflow.inputs.map((input) => input.name)), [workflow.inputs]);
  const toolsByName = useMemo(() => new Map(tools.map((tool) => [tool.name, tool])), [tools]);

  useEffect(() => {
    getTools()
      .then((result) => setTools(result.tools))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!focusStepId) return;
    setExpandedId(focusStepId);
    listRef.current
      ?.querySelector(`[data-step="${focusStepId}"]`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focusStepId]);

  const saveStep = (index: number, isNew: boolean) => async (updated: WorkflowStep) => {
    const steps = [...workflow.steps];
    if (isNew) steps.splice(index, 0, updated);
    else steps[index] = updated;
    let next: Workflow = { ...workflow, steps };
    const before = isNew ? null : stepOutput(workflow.steps[index]);
    const after = stepOutput(updated);
    if (before && after && before !== after) next = renameValue(next, before, after, index);
    const problem = await persist(next);
    if (!problem && isNew) {
      setPending(null);
      setExpandedId(null);
    }
    return problem;
  };

  const saveInputs = (inputs: WorkflowInput[], renames: [string, string][]) => {
    let next: Workflow = { ...workflow, inputs };
    for (const [from, to] of renames) next = renameValue(next, from, to, -1);
    return persist(next);
  };

  const openMenu = (index: number) => {
    setPending(null);
    setMenuAt(index);
  };

  const pick = (kind: StepKind, tool?: ToolInfo) => {
    if (menuAt === null) return;
    const step = newStep(kind, workflow, tool);
    setPending({ index: menuAt, step });
    setExpandedId(step.id);
    setMenuAt(null);
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    const problem = await persist({ ...workflow, steps: workflow.steps.filter((s) => s.id !== deleting.id) });
    if (problem) {
      setDeleteError(refusal("Can't delete it", problem));
    } else {
      setDeleting(null);
      setExpandedId(null);
    }
  };

  const cards: { step: WorkflowStep; index: number; isNew: boolean }[] = workflow.steps.map((step, index) => ({
    step,
    index,
    isNew: false,
  }));
  if (pending) {
    cards.splice(pending.index, 0, { step: pending.step, index: pending.index, isNew: true });
    for (let i = pending.index + 1; i < cards.length; i++) cards[i] = { ...cards[i], index: i };
  }

  const renderMenu = (index: number, placement: "below" | "above") =>
    menuAt === index && (
      <AddStepMenu tools={tools} placement={placement} onPick={pick} onClose={() => setMenuAt(null)} />
    );

  return (
    <div className="flex flex-col gap-7">
      <InputsEditor inputs={workflow.inputs} onSave={saveInputs} />
      <section>
        <SectionHeading aside={<KindLegend />}>Steps</SectionHeading>
        <ol ref={listRef} className="relative flex flex-col">
          <span aria-hidden="true" className="absolute bottom-6 left-[27px] top-6 w-px bg-[var(--border)]" />
          {cards.map(({ step, index, isNew }, position) => {
            const canInsert = position > 0 && !isNew && !cards[position - 1].isNew;
            return (
              <Fragment key={isNew ? `new-${step.id}` : step.id}>
                {canInsert ? (
                  <InsertPoint
                    label={`Add a step before step ${index + 1}`}
                    open={menuAt === index}
                    onOpen={() => openMenu(index)}
                  >
                    {renderMenu(index, "below")}
                  </InsertPoint>
                ) : (
                  <li className="h-2" aria-hidden="true" />
                )}
                <StepCard
                  step={step}
                  index={index}
                  total={workflow.steps.length}
                  isNew={isNew}
                  expanded={expandedId === step.id}
                  workflow={workflow}
                  tools={toolsByName}
                  inputNames={inputNames}
                  onToggle={() => setExpandedId((current) => (current === step.id ? null : step.id))}
                  onSave={saveStep(index, isNew)}
                  onMove={async (delta) =>
                    refusal("Can't move it there", await persist(moveStep(workflow, index, delta)))
                  }
                  onDelete={() => {
                    setDeleteError(null);
                    setDeleting(step);
                  }}
                  onCancelNew={() => {
                    setPending(null);
                    setExpandedId(null);
                  }}
                />
              </Fragment>
            );
          })}
        </ol>
        <div className="relative mt-3">
          <button
            type="button"
            aria-expanded={menuAt === workflow.steps.length}
            className="flex items-center gap-2 rounded-lg border border-dashed border-[var(--border-hover)] px-3 py-2 text-sm text-[var(--muted)] hover:border-[var(--fg)] hover:text-[var(--fg)]"
            onClick={() => openMenu(workflow.steps.length)}
          >
            <PlusIcon className="h-3.5 w-3.5" /> Add step
          </button>
          {renderMenu(workflow.steps.length, "below")}
        </div>
      </section>
      {deleting && (
        <ConfirmDialog
          title={`Delete "${deleting.title}"?`}
          description={
            deleteError ? (
              <span className="text-[var(--danger)]">{deleteError}</span>
            ) : (
              "Past runs keep their record of it. Later steps that read its result will need changing."
            )
          }
          confirmLabel={deleteError ? "OK" : "Delete"}
          tone={deleteError ? "neutral" : "danger"}
          onCancel={() => setDeleting(null)}
          onConfirm={deleteError ? () => setDeleting(null) : confirmDelete}
        />
      )}
    </div>
  );
}
