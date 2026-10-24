import { useCallback, useEffect, useRef, useState } from "react";
import { useComposerAction } from "../lib/menuCommands";
import { getEnvironments } from "../lib/rest";
import { useClickOutside } from "../lib/useClickOutside";
import type { Environment } from "../types/settings";
import { DEFAULT_ENVIRONMENT, EnvironmentDialog } from "./EnvironmentDialog";
import { CheckIcon, PlusIcon, SearchIcon, SettingsIcon } from "./icons";

interface EnvironmentPickerProps {
  /** The id of the environment this conversation (or task) uses. */
  current: string;
  /** While a turn runs: the turn keeps the environment it started with. */
  disabled: boolean;
  onPick: (id: string) => void;
  /** A scheduled task's form: its runs use the environment, and the menu's Choose Environment…
   * stays with the conversation. */
  forTask?: boolean;
}

/** The open picker: a search box, the environments with a gear each, and New environment (an
 * edit of null). */
function EnvironmentMenu({
  environments,
  current,
  query,
  onQuery,
  onPick,
  onEdit,
}: {
  environments: Environment[];
  current: string;
  query: string;
  onQuery: (query: string) => void;
  onPick: (id: string) => void;
  onEdit: (id: string | null) => void;
}) {
  const shown = environments.filter((e) => e.name.toLowerCase().includes(query.trim().toLowerCase()));
  return (
    <div className="absolute bottom-full left-0 z-20 mb-1 w-72 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] p-1 shadow-[var(--shadow)]">
      <div className="flex items-center gap-2 px-2.5 py-1.5">
        <SearchIcon className="h-4 w-4 shrink-0 text-[var(--muted)]" />
        <input
          aria-label="Search environments"
          autoFocus
          className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--muted)]"
          placeholder="Search environments"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
        />
      </div>
      <div className="mx-1 my-1 border-t border-[var(--border)]" />
      <ul className="max-h-64 overflow-y-auto">
        {shown.map((environment) => (
          <li key={environment.id} className="flex items-center rounded-md hover:bg-[var(--card-bg)]">
            <button
              type="button"
              role="menuitemradio"
              aria-checked={environment.id === current}
              className="flex min-w-0 flex-1 items-center gap-2.5 px-2.5 py-2 text-left text-sm"
              onClick={() => onPick(environment.id)}
            >
              <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                {environment.id === current && <CheckIcon className="h-4 w-4" />}
              </span>
              <span className="truncate">{environment.name}</span>
            </button>
            <button
              type="button"
              aria-label={`Edit ${environment.name}`}
              className="mr-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--bg)] hover:text-[var(--fg)]"
              onClick={() => onEdit(environment.id)}
            >
              <SettingsIcon className="h-4 w-4" />
            </button>
          </li>
        ))}
        {shown.length === 0 && <li className="px-2.5 py-2 text-sm text-[var(--muted)]">No environment matches.</li>}
      </ul>
      <div className="mx-1 my-1 border-t border-[var(--border)]" />
      <button
        type="button"
        className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm hover:bg-[var(--card-bg)]"
        onClick={() => onEdit(null)}
      >
        <PlusIcon className="h-4 w-4 shrink-0 text-[var(--muted)]" />
        New environment
      </button>
    </div>
  );
}

/** Which environment this conversation uses: search, pick, edit one with its gear, or make a new
 * one. An edit applies to every conversation using that environment. */
export function EnvironmentPicker({ current, disabled, onPick, forTask = false }: EnvironmentPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [editing, setEditing] = useState<string | null | undefined>(undefined);
  const rootRef = useRef<HTMLDivElement>(null);
  useClickOutside(rootRef, () => setOpen(false), open);

  const reload = useCallback(() => {
    getEnvironments()
      .then((result) => setEnvironments(result.environments))
      .catch(() => undefined);
  }, []);
  useEffect(reload, [reload, current]);
  useComposerAction("open-folder", () => {
    if (!disabled && !forTask) setOpen(true);
  });

  const chosen = environments.find((e) => e.id === current);
  const label = chosen?.name ?? (current === DEFAULT_ENVIRONMENT ? "Default" : "Environment");
  const pick = (id: string) => {
    setOpen(false);
    setQuery("");
    if (id !== current) onPick(id);
  };

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        disabled={disabled}
        title={
          forTask
            ? "Choose the environment this task's runs use"
            : disabled
              ? "The environment can't change while a reply is running"
              : "Choose this conversation's environment"
        }
        aria-expanded={open}
        className="flex h-8 max-w-48 items-center rounded-lg px-2 text-sm text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)] disabled:opacity-50 disabled:hover:bg-transparent"
        onClick={() => {
          if (!open) reload();
          setOpen((v) => !v);
        }}
      >
        <span className="truncate">{label}</span>
      </button>
      {open && (
        <EnvironmentMenu
          environments={environments}
          current={current}
          query={query}
          onQuery={setQuery}
          onPick={pick}
          onEdit={(id) => {
            setOpen(false);
            setEditing(id);
          }}
        />
      )}
      {editing !== undefined && (
        <EnvironmentDialog
          environmentId={editing}
          onSaved={(environment) => {
            reload();
            // A new one is made to be used here.
            if (editing === null && !disabled) onPick(environment.id);
          }}
          onDeleted={reload}
          onClose={() => setEditing(undefined)}
        />
      )}
    </div>
  );
}
