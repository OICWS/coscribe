import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { createEnvironment, deleteEnvironment, getEnvironments, getSecrets, updateEnvironment } from "../lib/rest";
import { folderName, remember } from "../lib/recentFolders";
import type { Environment, EnvironmentFields, SecretInfo } from "../types/settings";
import { ConfirmDialog } from "./ConfirmDialog";
import { EnvironmentRow, FoldersPage, SecretsPage, VariablesPage } from "./environment/EnvironmentPages";
import {
  runSummary,
  toVariables,
  useMachineLimits,
  useNetworkDraft,
  type MachineLimits,
  type RunSwitches,
  type VariableRow,
} from "./environment/drafts";
import { ArrowLeftIcon, CloseIcon } from "./icons";
import { NetworkPage } from "./environment/NetworkPage";
import { ScriptsPage } from "./environment/ScriptsPage";
import { fieldClass, primaryButtonClass, secondaryButtonClass } from "./settings/SettingRow";

type Page = "main" | "folders" | "network" | "scripts" | "secrets" | "variables";

const PAGE_TITLES: Record<Page, string> = {
  main: "Edit environment",
  folders: "Folders",
  network: "Network",
  scripts: "Scripts and commands",
  secrets: "Secrets",
  variables: "Environment variables",
};

export const DEFAULT_ENVIRONMENT = "default";

const BLANK: EnvironmentFields = {
  name: "",
  folders: [],
  network: { mode: "limited", domains: ["*"] },
  secrets: [],
  variables: {},
  scripts: true,
  commands: true,
  unisolated: false,
};

function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

function usedBy(environment: Environment | null): string {
  if (environment === null) return "A new environment";
  const tasks = environment.tasks > 0 ? ` and ${plural(environment.tasks, "scheduled task")}` : "";
  if (environment.id === DEFAULT_ENVIRONMENT) return `Used by every conversation that hasn't chosen another${tasks}`;
  return `Used by ${plural(environment.used_by, "conversation")}${tasks}`;
}

function whereItsConversationsGo(environment: Environment): string {
  const users = [environment.used_by, environment.tasks].some((n) => n > 0);
  return users ? "Its conversations and scheduled tasks move to Default." : "Nothing uses it.";
}

interface EnvironmentDialogProps {
  /** The environment to edit; null makes a new one. */
  environmentId: string | null;
  onSaved: (environment: Environment) => void;
  onDeleted?: () => void;
  onClose: () => void;
}

/** One environment: where its conversations work and what they may reach, one page per part.
 * Save keeps every page's edits at once. */
export function EnvironmentDialog(props: EnvironmentDialogProps) {
  const [loaded, setLoaded] = useState<{ environment: Environment | null; secrets: SecretInfo[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { environmentId, onClose } = props;

  useEffect(() => {
    let cancelled = false;
    Promise.all([getEnvironments(), getSecrets()])
      .then(([{ environments }, known]) => {
        if (cancelled) return;
        const environment = environments.find((e) => e.id === environmentId) ?? null;
        if (environmentId !== null && environment === null) setError("This environment no longer exists.");
        else setLoaded({ environment, secrets: known.secrets });
      })
      .catch(() => !cancelled && setError("Couldn't load the environment."));
    return () => {
      cancelled = true;
    };
  }, [environmentId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (loaded === null) {
    return createPortal(
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6" onClick={onClose}>
        <div className="rounded-2xl border border-[var(--border)] bg-[var(--panel-bg)] px-6 py-8 text-sm text-[var(--muted)]">
          {error ?? "Loading..."}
        </div>
      </div>,
      document.body,
    );
  }
  return <EnvironmentEditor {...props} environment={loaded.environment} secrets={loaded.secrets} />;
}

/** Every page's edits, kept until Save. */
function useEnvironmentDraft(start: EnvironmentFields) {
  const [name, setName] = useState(start.name);
  const [folders, setFolders] = useState(start.folders);
  const [chosen, setChosen] = useState(new Set(start.secrets));
  const [rows, setRows] = useState<VariableRow[]>(() =>
    Object.entries(start.variables).map(([key, value]) => ({ key, value })),
  );
  const [run, setRun] = useState<RunSwitches>({
    scripts: start.scripts,
    commands: start.commands,
    unisolated: start.unisolated,
  });
  const network = useNetworkDraft(start.network);
  const snapshot = () => JSON.stringify([name, folders, network.current, [...chosen].sort(), rows, run]);
  const [initialSnapshot] = useState(snapshot);
  const fields = (variables: Record<string, string>): EnvironmentFields => ({
    name: name.trim(),
    folders,
    network: network.current,
    secrets: [...chosen],
    variables,
    ...run,
  });
  const dirty = snapshot() !== initialSnapshot;
  return { name, setName, folders, setFolders, chosen, setChosen, rows, setRows, run, setRun, network, fields, dirty };
}

type EnvironmentDraft = ReturnType<typeof useEnvironmentDraft>;

function EditorHeader({
  page,
  environment,
  onBack,
  onClose,
}: {
  page: Page;
  environment: Environment | null;
  onBack: () => void;
  onClose: () => void;
}) {
  const iconButton =
    "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]";
  return (
    <div className="flex items-start gap-2 px-6 pb-3 pt-5">
      {page !== "main" && (
        <button type="button" aria-label="Back" className={`-ml-2 ${iconButton}`} onClick={onBack}>
          <ArrowLeftIcon className="h-4 w-4" />
        </button>
      )}
      <div className="min-w-0 flex-1">
        <h2 className="text-xl font-semibold leading-tight">
          {page === "main" && !environment ? "New environment" : PAGE_TITLES[page]}
        </h2>
        {page === "main" && <p className="mt-1 truncate text-sm text-[var(--muted)]">{usedBy(environment)}</p>}
      </div>
      <button type="button" aria-label="Close" className={iconButton} onClick={onClose}>
        <CloseIcon className="h-4 w-4" />
      </button>
    </div>
  );
}

function MainPage({
  draft,
  secrets,
  limits,
  onOpen,
}: {
  draft: EnvironmentDraft;
  secrets: SecretInfo[];
  limits: MachineLimits;
  onOpen: (page: Exclude<Page, "main">) => void;
}) {
  const { folders, chosen, rows } = draft;
  const variableCount = rows.filter((row) => row.key.trim()).length;
  const summaries: [Exclude<Page, "main">, string][] = [
    ["folders", folders.length === 0 ? "The default workspace" : folders.map(folderName).join(", ")],
    ["network", draft.network.summary],
    ["scripts", runSummary(draft.run, limits)],
    ["secrets", chosen.size === 0 ? "None" : `${chosen.size} of ${secrets.length}`],
    ["variables", variableCount === 0 ? "None" : `${variableCount} variable${variableCount === 1 ? "" : "s"}`],
  ];
  return (
    <div className="flex flex-col gap-3">
      <label className="flex flex-col gap-1.5">
        <span className="text-sm font-medium">Name</span>
        <input
          aria-label="Environment name"
          className={fieldClass}
          value={draft.name}
          maxLength={60}
          placeholder="e.g. Reports"
          onChange={(e) => draft.setName(e.target.value)}
        />
      </label>
      {summaries.map(([target, value]) => (
        <EnvironmentRow key={target} title={PAGE_TITLES[target]} value={value} onOpen={() => onOpen(target)} />
      ))}
    </div>
  );
}

function EditorFooter({
  deletable,
  saving,
  creating,
  onDelete,
  onCancel,
  onSave,
}: {
  deletable: boolean;
  saving: boolean;
  creating: boolean;
  onDelete: () => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <div className="flex items-center gap-2 border-t border-[var(--border)] px-6 py-4">
      {deletable && (
        <button
          type="button"
          className="mr-auto rounded-lg px-1 text-sm text-[var(--danger)] hover:underline"
          onClick={onDelete}
        >
          Delete environment
        </button>
      )}
      <button type="button" className={`${secondaryButtonClass} ${deletable ? "" : "ml-auto"}`} onClick={onCancel}>
        Cancel
      </button>
      <button type="button" className={primaryButtonClass} disabled={saving} onClick={onSave}>
        {saving ? "Saving…" : creating ? "Create" : "Save changes"}
      </button>
    </div>
  );
}

function EnvironmentEditor({
  environment,
  secrets,
  onSaved,
  onDeleted,
  onClose,
}: EnvironmentDialogProps & { environment: Environment | null; secrets: SecretInfo[] }) {
  const [page, setPage] = useState<Page>("main");
  const draft = useEnvironmentDraft(environment ?? BLANK);
  const limits = useMachineLimits();
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const deletable = environment !== null && environment.id !== DEFAULT_ENVIRONMENT;

  const save = async () => {
    const variables = toVariables(draft.rows);
    if (typeof variables === "string") {
      setError(`${variables} appears twice.`);
      setPage("variables");
      return;
    }
    if (!draft.name.trim()) {
      setError("Give the environment a name.");
      setPage("main");
      return;
    }
    setSaving(true);
    setError(null);
    const fields = draft.fields(variables);
    const result = environment ? await updateEnvironment(environment.id, fields) : await createEnvironment(fields);
    setSaving(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    remember(draft.folders);
    onSaved(result);
    onClose();
  };

  const remove = async () => {
    if (!environment) return;
    const result = await deleteEnvironment(environment.id);
    setConfirmDelete(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    onDeleted?.();
    onClose();
  };

  // A click beside the dialog closes it only when nothing is changed in it: a stray click must
  // not throw an edit away.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6" onClick={() => !draft.dirty && onClose()}>
      <div
        role="dialog"
        aria-label={environment ? "Edit environment" : "New environment"}
        className="flex max-h-full w-full max-w-[36rem] flex-col overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--panel-bg)] shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
      >
        <EditorHeader page={page} environment={environment} onBack={() => setPage("main")} onClose={onClose} />
        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-5 pt-1">
          {page === "main" && <MainPage draft={draft} secrets={secrets} limits={limits} onOpen={setPage} />}
          {page === "folders" && <FoldersPage folders={draft.folders} onChange={draft.setFolders} />}
          {page === "network" && <NetworkPage draft={draft.network} />}
          {page === "scripts" && <ScriptsPage run={draft.run} limits={limits} onChange={draft.setRun} />}
          {page === "secrets" && <SecretsPage secrets={secrets} chosen={draft.chosen} onChange={draft.setChosen} />}
          {page === "variables" && <VariablesPage rows={draft.rows} onChange={draft.setRows} />}
          {error && (
            <p role="alert" className="pt-4 text-sm text-[var(--danger)]">
              {error}
            </p>
          )}
        </div>
        <EditorFooter
          deletable={deletable}
          saving={saving}
          creating={environment === null}
          onDelete={() => setConfirmDelete(true)}
          onCancel={onClose}
          onSave={() => void save()}
        />
      </div>
      {confirmDelete && environment && (
        <ConfirmDialog
          title={`Delete ${environment.name}?`}
          description={whereItsConversationsGo(environment)}
          confirmLabel="Delete"
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() => void remove()}
        />
      )}
    </div>,
    document.body,
  );
}
