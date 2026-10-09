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
  const [loaded, setLoaded] = useState("");

  const snapshot = (current: VariableRow[], picked: Set<string>) =>
    JSON.stringify([current, [...picked].sort()]);
  const dirty = rows !== null && snapshot(rows, chosen) !== loaded;

  useEffect(() => {
    let cancelled = false;
    Promise.all([getSessionEnvironment(threadId), getSecrets()])
      .then(([environment, known]) => {
        if (cancelled) return;
        const loadedRows = Object.entries(environment.variables).map(([key, value]) => ({ key, value }));
        setRows(loadedRows);
        setChosen(new Set(environment.secrets));
        setLoaded(snapshot(loadedRows, new Set(environment.secrets)));
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

  // A click beside the dialog closes it only when nothing is typed in it: a stray
  // click must not throw an edit away.
  const closeFromBackdrop = () => {
    if (!dirty) onClose();
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6" onClick={closeFromBackdrop}>
      <div
        role="dialog"
        aria-label="Edit environment"
        className="flex max-h-full w-full max-w-[36rem] flex-col overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--panel-bg)] shadow-[var(--shadow)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-[var(--border)] px-6 py-4">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold leading-tight">Edit environment</h2>
            <p className="mt-0.5 truncate text-sm text-[var(--muted)]">For this conversation: {title}</p>
          </div>
          <button
            type="button"
            aria-label="Close"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
            onClick={onClose}
          >
            <CloseIcon className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {rows === null && !error && <p className="py-8 text-center text-sm text-[var(--muted)]">Loading...</p>}
          {rows !== null && (
            <div className="flex flex-col gap-7">
              <section>
                <h3 className="text-[15px] font-medium">Variables</h3>
                <p className="mt-1 text-[13px] leading-relaxed text-[var(--muted)]">
                  Plain text the assistant can read, such as a mode or a project name. For a key, use a secret below.
                  Scripts and the code module's commands get them as environment variables too, so a name such as PATH,
                  PYTHONPATH, NODE_OPTIONS or a proxy variable changes how they run.
                </p>
                {rows.length === 0 ? (
                  <p className="mt-3 rounded-xl border border-dashed border-[var(--border)] px-4 py-4 text-center text-sm text-[var(--muted)]">
                    No variables.
                  </p>
                ) : (
                  <div className="mt-3 flex flex-col gap-2">
                    <div className="flex gap-2 px-0.5 text-xs text-[var(--muted)]">
                      <span className="w-40">Name</span>
                      <span className="flex-1">Value</span>
                      <span className="w-9" />
                    </div>
                    {rows.map((row, index) => (
                      <div key={index} className="flex gap-2">
                        <input
                          aria-label="Variable name"
                          className={`${fieldClass} w-40 font-mono text-[13px]`}
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
                  </div>
                )}
                <button
                  type="button"
                  className="mt-3 flex w-fit items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--card-bg)]"
                  onClick={() => setRows([...rows, { key: "", value: "" }])}
                >
                  <PlusIcon className="h-4 w-4" /> Add variable
                </button>
              </section>

              <section>
                <h3 className="text-[15px] font-medium">Secrets</h3>
                <p className="mt-1 text-[13px] leading-relaxed text-[var(--muted)]">
                  The assistant never sees a secret's value. It can use the ones you tick in a web request, for the
                  hosts shown. None by default.
                </p>
                {secrets.length === 0 ? (
                  <p className="mt-3 rounded-xl border border-dashed border-[var(--border)] px-4 py-4 text-center text-sm text-[var(--muted)]">
                    No secrets yet. Add them in Settings &gt; Secrets.
                  </p>
                ) : (
                  <ul className="mt-3 flex flex-col gap-2">
                    {secrets.map((secret) => (
                      <li key={secret.name}>
                        <label
                          className={`flex cursor-pointer items-start gap-3 rounded-xl border px-3.5 py-3 hover:bg-[var(--card-bg)] ${
                            chosen.has(secret.name) ? "border-[var(--accent)]" : "border-[var(--border)]"
                          }`}
                        >
                          <input
                            type="checkbox"
                            className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
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
                            <span className="mt-1 flex flex-wrap gap-1">
                              {secret.hosts.map((host) => (
                                <span
                                  key={host}
                                  className="rounded-md bg-[var(--card-bg)] px-1.5 py-0.5 font-mono text-xs text-[var(--muted)]"
                                >
                                  {host}
                                </span>
                              ))}
                            </span>
                          </span>
                        </label>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
          )}
          {error && (
            <p role="alert" className="pt-4 text-sm text-[var(--danger)]">
              {error}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-[var(--border)] px-6 py-4">
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
