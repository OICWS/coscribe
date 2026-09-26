import { useEffect, useState } from "react";
import { primaryButton, secondaryButton } from "../../lib/formStyles";
import { draftWorkflowFromThread, validateWorkflow, type WorkflowDraftResult } from "../../lib/rest";
import type { Workflow } from "../../types/workflow";
import { ConfirmDialog } from "../ConfirmDialog";
import { AlertCircleIcon, ArrowLeftIcon } from "../icons";
import { WorkflowEditor } from "./WorkflowEditor";

type DraftState =
  | { status: "drafting" }
  | { status: "failed"; error: string }
  | ({ status: "ready" } & WorkflowDraftResult);

interface WorkflowDraftPageProps {
  threadId: string;
  threadTitle: string;
  nameHint: string;
  /** A draft already made (by the model, in the conversation) -- shown
   * as is, not drafted again. */
  initial?: WorkflowDraftResult;
  /** Proposed changes to a saved task's workflow, rather than a new one. */
  revision?: { triggerId: string; changes: string[] };
  /** Saves a revision in place; resolves to an error message, or null. */
  onSaveRevision?: (triggerId: string, workflow: Workflow) => Promise<string | null>;
  onBack: () => void;
  onDiscard: () => void;
  onSave: (name: string, workflow: Workflow) => void;
}

const serif = { fontFamily: "Georgia, 'Times New Roman', serif" };

function BackLink({ onBack }: { onBack: () => void }) {
  return (
    <button
      type="button"
      className="-ml-1.5 flex items-center gap-1.5 rounded-md px-1.5 py-1 text-sm text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
      onClick={onBack}
    >
      <ArrowLeftIcon className="h-3.5 w-3.5" />
      Back to conversation
    </button>
  );
}

function Drafting() {
  return (
    <div className="mt-10 flex flex-col items-center text-center" role="status">
      <span className="h-7 w-7 animate-spin rounded-full border-[3px] border-[color-mix(in_srgb,var(--fg)_15%,transparent)] border-t-[var(--accent)] motion-reduce:animate-none" />
      <h1 className="mt-5 text-xl font-semibold" style={serif}>
        Drafting a workflow
      </h1>
      <p className="mt-1.5 max-w-md text-sm text-[var(--muted)]">
        Reading this conversation’s tool calls and keeping the ones that did the work. This usually takes under a
        minute.
      </p>
      <ol aria-hidden="true" className="mt-9 flex w-full max-w-xl flex-col gap-2">
        {[0.9, 0.7, 0.8].map((width, i) => (
          <li
            key={i}
            className="flex items-center gap-3.5 rounded-xl border border-[var(--border)] px-3.5 py-3 motion-safe:animate-pulse"
            style={{ animationDelay: `${i * 150}ms` }}
          >
            <span className="h-7 w-7 shrink-0 rounded-lg bg-[var(--card-bg)]" />
            <span className="flex flex-1 flex-col gap-1.5">
              <span className="h-2.5 rounded bg-[var(--card-bg)]" style={{ width: `${width * 45}%` }} />
              <span className="h-2 rounded bg-[var(--card-bg)]" style={{ width: `${width * 75}%` }} />
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** A workflow distilled from a conversation, reviewed in the step editor
 * before it becomes a task. Edits are checked by the server but only kept
 * here, so leaving the page drops the draft. */
export function WorkflowDraftPage({
  threadId,
  threadTitle,
  nameHint,
  initial,
  revision,
  onSaveRevision,
  onBack,
  onDiscard,
  onSave,
}: WorkflowDraftPageProps) {
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<DraftState>(initial ? { status: "ready", ...initial } : { status: "drafting" });
  const [attempt, setAttempt] = useState(0);
  const [confirmingDiscard, setConfirmingDiscard] = useState(false);

  useEffect(() => {
    if (initial) return;
    let current = true;
    setDraft({ status: "drafting" });
    draftWorkflowFromThread(threadId, nameHint)
      .then((result) => {
        if (!current) return;
        setDraft("error" in result ? { status: "failed", error: result.error } : { status: "ready", ...result });
      })
      .catch(() => {
        if (current) setDraft({ status: "failed", error: "Couldn't reach coscribe. Check that it's still running." });
      });
    return () => {
      current = false;
    };
  }, [threadId, nameHint, attempt, initial]);

  const persist = async (workflow: Workflow) => {
    const result = await validateWorkflow(workflow);
    if ("error" in result) return result.error;
    setDraft((prev) => (prev.status === "ready" ? { ...prev, workflow: result.workflow } : prev));
    return null;
  };

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-3xl">
        <BackLink onBack={onBack} />

        {draft.status === "drafting" && <Drafting />}

        {draft.status === "failed" && (
          <div className="mt-10 flex flex-col items-center text-center">
            <span className="flex h-10 w-10 items-center justify-center rounded-full bg-[color-mix(in_srgb,var(--danger)_12%,transparent)] text-[var(--danger)]">
              <AlertCircleIcon className="h-5 w-5" />
            </span>
            <h1 className="mt-4 text-xl font-semibold" style={serif}>
              Couldn’t draft a workflow
            </h1>
            <p role="alert" className="mt-1.5 max-w-md text-sm text-[var(--muted)]">
              {draft.error}
            </p>
            <div className="mt-5 flex gap-2">
              <button type="button" className={secondaryButton} onClick={onBack}>
                Back to conversation
              </button>
              <button type="button" className={primaryButton} onClick={() => setAttempt((n) => n + 1)}>
                Try again
              </button>
            </div>
          </div>
        )}

        {draft.status === "ready" && (
          <>
            <div className="mt-4 flex items-start justify-between gap-6 max-sm:flex-col">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-xs text-[var(--muted)]">
                  <span className="rounded-full bg-[var(--accent-soft)] px-2 py-0.5 font-medium text-[var(--accent-ink)]">
                    {revision ? "Changes" : "Draft"}
                  </span>
                  <span className="truncate" title={threadTitle}>
                    From “{threadTitle}”
                  </span>
                </div>
                <h1 className="mt-2 truncate text-2xl font-semibold" style={serif}>
                  {draft.name}
                </h1>
                <p className="mt-1 text-sm text-[var(--muted)]">
                  {revision
                    ? "The task is unchanged until you save. Check the steps, then save the changes."
                    : "Nothing is saved yet. Check each step, then save it as a task."}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <button type="button" className={secondaryButton} onClick={() => setConfirmingDiscard(true)}>
                  Discard
                </button>
                {revision && onSaveRevision ? (
                  <button
                    type="button"
                    className={primaryButton}
                    disabled={draft.workflow.steps.length === 0 || saving}
                    onClick={async () => {
                      setSaving(true);
                      setSaveError(await onSaveRevision(revision.triggerId, draft.workflow));
                      setSaving(false);
                    }}
                  >
                    {saving ? "Saving…" : "Save changes"}
                  </button>
                ) : (
                  <button
                    type="button"
                    className={primaryButton}
                    disabled={draft.workflow.steps.length === 0}
                    onClick={() => onSave(draft.name, draft.workflow)}
                  >
                    Save as task…
                  </button>
                )}
              </div>
            </div>

            {saveError && (
              <p role="alert" className="mt-4 text-sm text-[var(--danger)]">
                {saveError}
              </p>
            )}

            {revision && revision.changes.length > 0 && (
              <section
                aria-label="What changes"
                className="mt-5 rounded-xl border border-[var(--border)] px-4 py-3"
              >
                <h2 className="text-sm font-medium">What changes</h2>
                <ul className="mt-1.5 flex list-disc flex-col gap-1 pl-6 text-[13px] leading-relaxed marker:text-[var(--muted)]">
                  {revision.changes.map((change, i) => (
                    <li key={i}>{change}</li>
                  ))}
                </ul>
              </section>
            )}

            {draft.notes.length > 0 && (
              <aside
                aria-label="Worth checking"
                className="mt-5 rounded-xl border border-[color-mix(in_srgb,var(--warning)_35%,var(--border))] bg-[color-mix(in_srgb,var(--warning)_7%,transparent)] px-4 py-3"
              >
                <h2 className="flex items-center gap-2 text-sm font-medium">
                  <AlertCircleIcon className="h-4 w-4 text-[var(--warning)]" />
                  Worth checking before you save
                </h2>
                <ul className="mt-1.5 flex list-disc flex-col gap-1 pl-6 text-[13px] leading-relaxed text-[var(--fg)] marker:text-[var(--muted)]">
                  {draft.notes.map((note, i) => (
                    <li key={i}>{note}</li>
                  ))}
                </ul>
              </aside>
            )}

            <div className="mt-6 border-t border-[var(--border)]" />
            <div className="mt-6">
              <WorkflowEditor workflow={draft.workflow} persist={persist} />
            </div>
          </>
        )}
      </div>

      {confirmingDiscard && (
        <ConfirmDialog
          title={revision ? "Discard these changes?" : "Discard this draft?"}
          description={
            revision
              ? "The task stays as it is. You can ask for the changes again in the conversation."
              : "You can draft it again from the conversation, but your edits here will be lost."
          }
          confirmLabel="Discard"
          tone="danger"
          onCancel={() => setConfirmingDiscard(false)}
          onConfirm={onDiscard}
        />
      )}
    </div>
  );
}
