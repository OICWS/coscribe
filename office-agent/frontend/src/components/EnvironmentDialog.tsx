import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { getSecrets, getSessionEnvironment, saveSessionEnvironment } from "../lib/rest";
import type { SecretInfo } from "../types/settings";
import { CloseIcon, PlusIcon } from "./icons";
import { fieldClass, primaryButtonClass, secondaryButtonClass } from "./settings/SettingRow";

interface VariableRow {
  key: string;
  value: string;
}

/** Right-click a conversation > Edit environment: variables the assistant can read, and which of the global
 * secrets this conversation may use. */
export function EnvironmentDialog({
  threadId,
  title,
  onClose,
}: {
  threadId: string;
  title: string;
  onClose: () => void;
}) {
  const [rows, setRows] = useState<VariableRow[] | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [secrets, setSecrets] = useState<SecretInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getSessionEnvironment(threadId), getSecrets()])
      .then(([environment, known]) => {
        if (cancelled) return;
        setRows(Object.entries(environment.variables).map(([key, value]) => ({ key, value })));
        setChosen(new Set(environment.secrets));
        setSecrets(known.secrets);
      })
      .catch(() => !cancelled && setError("Couldn't load this conversation's environment."));
    return () => {
      cancelled = true;
    };
  }, [threadId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const update = (index: number, patch: Partial<VariableRow>) =>
    setRows((current) => current?.map((row, i) => (i === index ? { ...row, ...patch } : row)) ?? current);

  const save = async () => {
    if (!rows) return;
    const variables: Record<string, string> = {};
    for (const row of rows) {
      const key = row.key.trim();
      if (!key) continue;
      if (key in variables) {
        setError(`${key} appears twice.`);
        return;
      }
      variables[key] = row.value;
    }
    setSaving(true);
    setError(null);
    const result = await saveSessionEnvironment(threadId, { variables, secrets: [...chosen] });
    setSaving(false);
    if ("error" in result) setError(result.error);
    else onClose();
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6" onClick={onClose}>
      <div
        role="dialog"
        aria-label="Edit environment"
        className="flex max-h-full w-full max-w-[34rem] flex-col overflow-hidden rounded-3xl bg-[var(--panel-bg)] shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 px-7 pb-2 pt-6">
          <div className="min-w-0">
            <h2 className="text-2xl font-semibold leading-tight">Edit environment</h2>
            <p className="mt-1 truncate text-sm text-[var(--muted)]">{title}</p>
          </div>
          <button
            type="button"
            aria-label="Close"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[var(--border)] hover:bg-[var(--card-bg)]"
            onClick={onClose}
          >
            <CloseIcon className="h-5 w-5" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-7 pb-2">
          {rows === null && !error && <p className="py-8 text-center text-sm text-[var(--muted)]">Loading...</p>}
          {rows !== null && (
            <div className="flex flex-col gap-6 pt-3">
              <section>
                <h3 className="text-[15px] font-medium">Variables</h3>
                <p className="mt-0.5 text-xs text-[var(--muted)]">
                  Plain text the assistant can read, such as a mode or a project name. Don't put a key here: use a
                  secret. Scripts and the code module's commands get them as environment variables too, so a name
                  such as PATH, PYTHONPATH, NODE_OPTIONS or a proxy variable changes how they run.
                </p>
                <div className="mt-3 flex flex-col gap-2">
                  {rows.map((row, index) => (
                    <div key={index} className="flex gap-2">
                      <input
                        aria-label="Variable name"
                        className={`${fieldClass} w-40 font-mono`}
                        value={row.key}
                        placeholder="NAME"
                        spellCheck={false}
                        onChange={(e) => update(index, { key: e.target.value })}
                      />
                      <input
                        aria-label="Variable value"
                        className={`${fieldClass} min-w-0 flex-1`}
                        value={row.value}
                        placeholder="value"
                        onChange={(e) => update(index, { value: e.target.value })}
                      />
                      <button
                        type="button"
                        aria-label="Remove variable"
                        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
                        onClick={() => setRows(rows.filter((_, i) => i !== index))}
                      >
                        <CloseIcon className="h-4 w-4" />
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    className="flex w-fit items-center gap-1.5 rounded-lg px-2 py-1 text-sm text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
                    onClick={() => setRows([...rows, { key: "", value: "" }])}
                  >
                    <PlusIcon className="h-4 w-4" /> Add variable
                  </button>
                </div>
              </section>

              <section>
                <h3 className="text-[15px] font-medium">Secrets</h3>
                <p className="mt-0.5 text-xs text-[var(--muted)]">
                  The assistant never sees a secret's value. It can use the ones you tick in a web request, for the hosts
                  shown. None by default.
                </p>
                <div className="mt-3 flex flex-col">
                  {secrets.length === 0 && (
                    <p className="text-sm text-[var(--muted)]">No secrets yet. Add them in Settings &gt; Secrets.</p>
                  )}
                  {secrets.map((secret) => (
                    <label key={secret.name} className="flex cursor-pointer items-start gap-3 rounded-lg px-2 py-2 hover:bg-[var(--card-bg)]">
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={chosen.has(secret.name)}
                        onChange={(e) =>
                          setChosen((current) => {
                            const next = new Set(current);
                            if (e.target.checked) next.add(secret.name);
                            else next.delete(secret.name);
                            return next;
                          })
                        }
                      />
                      <span className="min-w-0">
                        <span className="block font-mono text-sm">{secret.name}</span>
                        <span className="block truncate text-xs text-[var(--muted)]">{secret.hosts.join(", ")}</span>
                      </span>
                    </label>
                  ))}
                </div>
              </section>
            </div>
          )}
          {error && (
            <p role="alert" className="pb-2 pt-3 text-sm text-[var(--danger)]">
              {error}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 px-7 pb-6 pt-4">
          <button type="button" className={secondaryButtonClass} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className={primaryButtonClass} disabled={rows === null || saving} onClick={() => void save()}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
