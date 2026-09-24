import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { inputClass, primaryButton, secondaryButton } from "../../lib/formStyles";
import { getTools } from "../../lib/rest";
import { moveStep, newStep, renameValue } from "../../lib/workflowEdit";
import { KIND_COLOR, stepOutput } from "../../lib/workflowLabels";
import {
  countInside,
  isBlock,
  listAt,
  listKey,
  placeSteps,
  ROOT,
  scopeAt,
  scopes,
  withList,
  type Arm,
  type ListRef,
} from "../../lib/workflowTree";
import type { ToolInfo } from "../../types/settings";
import type { StepKind, Workflow, WorkflowInput, WorkflowStep } from "../../types/workflow";
import { ConfirmDialog } from "../ConfirmDialog";
import { ArrowDownIcon, ArrowUpIcon, ChevronDownIcon, PlusIcon, TrashIcon } from "../icons";
import { AddStepMenu } from "./AddStepMenu";
import { FieldLabel } from "./fields";
import { InputsEditor } from "./InputsEditor";
import { KindLegend, SectionHeading, StepKindTile } from "./parts";
import { StepFields, type EditorContext } from "./StepEditors";
import { StepSummary } from "./StepSummary";

/** Saves (or, for a draft, only checks) the whole workflow; resolves to
 * the server's refusal, or null once it holds. */
export type Persist = (workflow: Workflow) => Promise<string | null>;

/** The server's validation message, led by what the edit was. */
function refusal(action: string, problem: string | null): string | null {
  if (!problem) return null;
  return `${action} -- ${problem.replace(/^The workflow isn't valid: /, "")}`;
}

/** A step without the steps it holds -- what its own editor changes. */
function ownFields(step: WorkflowStep): string {
  if (step.kind === "branch") return JSON.stringify({ ...step, then: null, otherwise: null });
  if (step.kind === "loop") return JSON.stringify({ ...step, steps: null });
  return JSON.stringify(step);
}

/** `edited`'s own fields with `current`'s children: the steps inside a
 * block are saved from their own cards while the block is open. */
function keepChildren(edited: WorkflowStep, current: WorkflowStep | undefined): WorkflowStep {
  if (edited.kind === "branch" && current?.kind === "branch") {
    return { ...edited, then: current.then, otherwise: current.otherwise };
  }
  if (edited.kind === "loop" && current?.kind === "loop") return { ...edited, steps: current.steps };
  return edited;
}

const ARM_LABEL: Record<Arm, string> = { then: "Then", otherwise: "Otherwise", steps: "For each" };

interface StepCardProps {
  step: WorkflowStep;
  number: string;
  index: number;
  total: number;
  isNew: boolean;
  expanded: boolean;
  context: EditorContext;
  canDelete: boolean;
  onToggle: () => void;
  onSave: (step: WorkflowStep) => Promise<string | null>;
  onMove: (delta: -1 | 1) => Promise<string | null>;
  onDelete: () => void;
  onCancelNew: () => void;
  /** A block's own step lists, shown under its header. */
  children?: ReactNode;
}

function StepCard({
  step,
  number,
  index,
  total,
  isNew,
  expanded,
  context,
  canDelete,
  onToggle,
  onSave,
  onMove,
  onDelete,
  onCancelNew,
  children,
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

  const dirty = isNew || ownFields(draft) !== ownFields(step);
  let saveLabel = isNew ? "Add step" : "Save step";
  if (busy) saveLabel = "Saving…";

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
                <StepSummary step={step} inputNames={context.inputNames} />
              </span>
            )}
          </span>
          {expanded && step.kind === "llm" && (
            <span className="hidden shrink-0 text-xs text-[var(--muted)] sm:inline">
              {step.model ?? "Task's model"} · temperature 0
            </span>
          )}
          <span className="min-w-5 shrink-0 text-right text-xs tabular-nums text-[var(--muted)]">{number}</span>
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
              disabled={busy || !canDelete}
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
      {children}
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

/** Everything a list of steps needs from the editor, passed down whole to
 * the lists nested inside branches and loops. */
interface ListApi {
  numbers: Map<string, number>;
  scopeMap: Map<string, string[]>;
  inputNames: Set<string>;
  tools: ToolInfo[];
  toolsByName: Map<string, ToolInfo>;
  expandedId: string | null;
  pending: { list: ListRef; index: number; step: WorkflowStep } | null;
  menuKey: string | null;
  toggle: (id: string) => void;
  openMenu: (list: ListRef, index: number) => void;
  closeMenu: () => void;
  pick: (list: ListRef, index: number, kind: StepKind, tool?: ToolInfo) => void;
  save: (list: ListRef, index: number, isNew: boolean) => (step: WorkflowStep) => Promise<string | null>;
  move: (list: ListRef, index: number, delta: -1 | 1) => Promise<string | null>;
  askDelete: (step: WorkflowStep, list: ListRef) => void;
  cancelNew: () => void;
}

function menuKeyOf(list: ListRef, index: number): string {
  return `${listKey(list)}#${index}`;
}

function AddStepButton({ api, list, index, nested }: { api: ListApi; list: ListRef; index: number; nested: boolean }) {
  const open = api.menuKey === menuKeyOf(list, index);
  return (
    <div className="relative mt-2">
      <button
        type="button"
        aria-expanded={open}
        className={`flex items-center gap-2 rounded-lg border border-dashed border-[var(--border-hover)] text-[var(--muted)] hover:border-[var(--fg)] hover:text-[var(--fg)] ${
          nested ? "px-2.5 py-1.5 text-[13px]" : "px-3 py-2 text-sm"
        }`}
        onClick={() => api.openMenu(list, index)}
      >
        <PlusIcon className="h-3.5 w-3.5" /> Add step
      </button>
      {open && (
        <AddStepMenu
          tools={api.tools}
          placement="below"
          onPick={(kind, tool) => api.pick(list, index, kind, tool)}
          onClose={api.closeMenu}
        />
      )}
    </div>
  );
}

/** The lists a branch or loop holds, under its header. */
function BlockLists({ step, api }: { step: WorkflowStep; api: ListApi }) {
  if (!isBlock(step)) return null;
  const color = KIND_COLOR[step.kind];
  const arms: [Arm, WorkflowStep[], string][] =
    step.kind === "branch"
      ? [
          ["then", step.then, "when it holds"],
          ["otherwise", step.otherwise, "when it doesn't"],
        ]
      : [["steps", step.steps, `${step.item} in ${step.over || "…"}`]];
  return (
    <div className="flex flex-col gap-2.5 px-3 pb-3">
      {arms.map(([arm, steps, note]) => (
        <section
          key={arm}
          aria-label={`${step.title}: ${ARM_LABEL[arm]}`}
          className="rounded-lg border-l-2 py-2.5 pl-3 pr-2.5"
          style={{
            borderLeftColor: `color-mix(in srgb, ${color} 55%, transparent)`,
            backgroundColor: `color-mix(in srgb, ${color} 4%, transparent)`,
          }}
        >
          <div className="mb-1 flex items-baseline gap-2 text-xs">
            <span className="font-semibold uppercase tracking-[0.08em]" style={{ color }}>
              {ARM_LABEL[arm]}
            </span>
            <span className="truncate font-mono text-[var(--muted)]">{note}</span>
          </div>
          <StepList api={api} list={{ parent: step.id, arm }} steps={steps} nested />
        </section>
      ))}
    </div>
  );
}

function StepList({
  api,
  list,
  steps,
  nested,
}: {
  api: ListApi;
  list: ListRef;
  steps: WorkflowStep[];
  nested: boolean;
}) {
  const pendingHere = api.pending && listKey(api.pending.list) === listKey(list) ? api.pending : null;
  const cards = steps.map((step, index) => ({ step, index, isNew: false }));
  if (pendingHere) {
    cards.splice(pendingHere.index, 0, { step: pendingHere.step, index: pendingHere.index, isNew: true });
    for (let i = pendingHere.index + 1; i < cards.length; i++) cards[i] = { ...cards[i], index: i };
  }

  const contextFor = (step: WorkflowStep, index: number): EditorContext => {
    const suggestions = scopeAt(api.scopeMap, list, index);
    return {
      suggestions,
      inputNames: api.inputNames,
      tools: api.toolsByName,
      listId: `values-${step.id}`,
      collectable:
        step.kind === "loop"
          ? scopeAt(api.scopeMap, { parent: step.id, arm: "steps" }, step.steps.length).filter(
              (name) => !suggestions.includes(name),
            )
          : undefined,
    };
  };

  return (
    <>
      {cards.length === 0 && nested && <p className="py-1 text-[13px] text-[var(--muted)]">No steps yet.</p>}
      {cards.length > 0 && (
        <ol className="relative flex flex-col">
          <span aria-hidden="true" className="absolute bottom-6 left-[27px] top-6 w-px bg-[var(--border)]" />
          {cards.map(({ step, index, isNew }, position) => {
            const canInsert = position > 0 && !isNew && !cards[position - 1].isNew;
            const insertOpen = api.menuKey === menuKeyOf(list, index);
            return (
              <Fragment key={isNew ? `new-${step.id}` : step.id}>
                {canInsert ? (
                  <InsertPoint
                    label={`Add a step before step ${api.numbers.get(step.id) ?? index + 1}`}
                    open={insertOpen}
                    onOpen={() => api.openMenu(list, index)}
                  >
                    {insertOpen && (
                      <AddStepMenu
                        tools={api.tools}
                        placement="below"
                        onPick={(kind, tool) => api.pick(list, index, kind, tool)}
                        onClose={api.closeMenu}
                      />
                    )}
                  </InsertPoint>
                ) : (
                  <li className="h-2" aria-hidden="true" />
                )}
                <StepCard
                  step={step}
                  number={isNew ? "New" : String(api.numbers.get(step.id) ?? "")}
                  index={index}
                  total={steps.length}
                  isNew={isNew}
                  expanded={api.expandedId === step.id}
                  context={contextFor(step, index)}
                  canDelete={nested || steps.length > 1}
                  onToggle={() => api.toggle(step.id)}
                  onSave={api.save(list, index, isNew)}
                  onMove={(delta) => api.move(list, index, delta)}
                  onDelete={() => api.askDelete(step, list)}
                  onCancelNew={api.cancelNew}
                >
                  {!isNew && <BlockLists step={step} api={api} />}
                </StepCard>
              </Fragment>
            );
          })}
        </ol>
      )}
      <AddStepButton api={api} list={list} index={steps.length} nested={nested} />
    </>
  );
}

/** A workflow's inputs and steps (the Edit artboard of
 * https://claude.ai/artifact/4G5JyZ3r4tMPF6QcFG3Vj1). One step open at a
 * time; every change goes through `persist` with the whole workflow, which
 * the server re-validates -- so a refusal (a later step reading a deleted
 * result, a move past a value's source) comes back to the step that
 * caused it. Branches and loops hold their own lists, edited the same way
 * inside their cards. */
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
  const [pending, setPending] = useState<ListApi["pending"]>(null);
  const [menuKey, setMenuKey] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<{ step: WorkflowStep; list: ListRef } | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const rootRef = useRef<HTMLElement>(null);
  const inputNames = useMemo(() => new Set(workflow.inputs.map((input) => input.name)), [workflow.inputs]);
  const toolsByName = useMemo(() => new Map(tools.map((tool) => [tool.name, tool])), [tools]);
  const numbers = useMemo(
    () => new Map(placeSteps(workflow.steps).map((placed) => [placed.step.id, placed.number])),
    [workflow.steps],
  );
  const scopeMap = useMemo(() => scopes(workflow), [workflow]);

  useEffect(() => {
    getTools()
      .then((result) => setTools(result.tools))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!focusStepId) return;
    setExpandedId(focusStepId);
    rootRef.current
      ?.querySelector(`[data-step="${focusStepId}"]`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focusStepId]);

  const save = (list: ListRef, index: number, isNew: boolean) => async (edited: WorkflowStep) => {
    const steps = [...listAt(workflow, list)];
    const current = isNew ? undefined : steps[index];
    let updated = keepChildren(edited, current);
    const itemRename =
      current?.kind === "loop" && updated.kind === "loop" && current.item !== updated.item
        ? { from: current.item, to: updated.item }
        : null;
    if (itemRename && updated.kind === "loop" && updated.collect?.split(".")[0] === itemRename.from) {
      updated = { ...updated, collect: itemRename.to + updated.collect.slice(itemRename.from.length) };
    }
    if (isNew) steps.splice(index, 0, updated);
    else steps[index] = updated;
    let next = withList(workflow, list, steps);
    const before = current ? stepOutput(current) : null;
    const after = stepOutput(updated);
    if (before && after && before !== after) next = renameValue(next, before, after, updated.id);
    if (itemRename) next = renameValue(next, itemRename.from, itemRename.to, updated.id);
    const problem = await persist(next);
    if (!problem && isNew) {
      setPending(null);
      setExpandedId(null);
    }
    return problem;
  };

  const saveInputs = (inputs: WorkflowInput[], renames: [string, string][]) => {
    let next: Workflow = { ...workflow, inputs };
    for (const [from, to] of renames) next = renameValue(next, from, to, null);
    return persist(next);
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    const { step, list } = deleting;
    const remaining = listAt(workflow, list).filter((s) => s.id !== step.id);
    const problem = await persist(withList(workflow, list, remaining));
    if (problem) {
      setDeleteError(refusal("Can't delete it", problem));
    } else {
      setDeleting(null);
      setExpandedId(null);
    }
  };

  const api: ListApi = {
    numbers,
    scopeMap,
    inputNames,
    tools,
    toolsByName,
    expandedId,
    pending,
    menuKey,
    toggle: (id) => setExpandedId((current) => (current === id ? null : id)),
    openMenu: (list, index) => {
      setPending(null);
      setMenuKey(menuKeyOf(list, index));
    },
    closeMenu: () => setMenuKey(null),
    pick: (list, index, kind, tool) => {
      const step = newStep(kind, workflow, scopeAt(scopeMap, list, index), tool);
      setPending({ list, index, step });
      setExpandedId(step.id);
      setMenuKey(null);
    },
    save,
    move: async (list, index, delta) =>
      refusal("Can't move it there", await persist(moveStep(workflow, list, index, delta))),
    askDelete: (step, list) => {
      setDeleteError(null);
      setDeleting({ step, list });
    },
    cancelNew: () => {
      setPending(null);
      setExpandedId(null);
    },
  };

  const inside = deleting ? countInside(deleting.step) : 0;
  let deleteDescription = "Past runs keep their record of it. Later steps that read its result will need changing.";
  if (inside > 0) {
    deleteDescription = `The ${inside} ${inside === 1 ? "step" : "steps"} inside it go too. Past runs keep their record.`;
  }

  return (
    <div className="flex flex-col gap-7">
      <InputsEditor inputs={workflow.inputs} onSave={saveInputs} />
      <section ref={rootRef}>
        <SectionHeading aside={<KindLegend />}>Steps</SectionHeading>
        <StepList api={api} list={ROOT} steps={workflow.steps} nested={false} />
      </section>
      {deleting && (
        <ConfirmDialog
          title={`Delete "${deleting.step.title}"?`}
          description={deleteError ? <span className="text-[var(--danger)]">{deleteError}</span> : deleteDescription}
          confirmLabel={deleteError ? "OK" : "Delete"}
          tone={deleteError ? "neutral" : "danger"}
          onCancel={() => setDeleting(null)}
          onConfirm={deleteError ? () => setDeleting(null) : confirmDelete}
        />
      )}
    </div>
  );
}
