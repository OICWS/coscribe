import { useState } from "react";
import { deleteSecret, getSecrets, saveSecret } from "../../lib/rest";
import { useFetchOnActive } from "../../lib/useFetchOnActive";
import type { SecretInfo, SecretsResponse } from "../../types/settings";
import { ConfirmDialog } from "../ConfirmDialog";
import { AlertCircleIcon, PlusIcon } from "../icons";
import { FetchRetry } from "./FetchRetry";
import { fieldClass, primaryButtonClass, secondaryButtonClass } from "./SettingRow";

const EMPTY: SecretsResponse = { keychain: true, secrets: [] };

/** What the form is doing: adding a secret, replacing a value, or changing where one may be sent. */
type FormMode = { kind: "add" } | { kind: "replace"; secret: SecretInfo } | { kind: "hosts"; secret: SecretInfo };

function parseHosts(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((host) => host.trim())
    .filter(Boolean);
}

function shortDate(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function SecretForm({
  mode,
  onDone,
  onCancel,
}: {
  mode: FormMode;
  onDone: () => void;
  onCancel: () => void;
}) {
  const existing = mode.kind === "add" ? null : mode.secret;
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [hosts, setHosts] = useState(existing ? existing.hosts.join("\n") : "");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const needsValue = mode.kind !== "hosts";
  const needsHosts = mode.kind !== "replace";
  const secretName = existing?.name ?? name.trim();

  const save = async () => {
    setSaving(true);
    setError(null);
    const body: { value?: string; hosts: string[] } = {
      hosts: mode.kind === "replace" ? mode.secret.hosts : parseHosts(hosts),
    };
    if (needsValue) body.value = value;
    const result = await saveSecret(secretName, body);
    setSaving(false);
    if ("error" in result) setError(result.error);
    else onDone();
  };

  const ready = secretName !== "" && (!needsValue || value !== "") && (!needsHosts || parseHosts(hosts).length > 0);
  const title =
    mode.kind === "add" ? "Add a secret" : mode.kind === "replace" ? `Replace the value of ${mode.secret.name}` : `Where ${mode.secret.name} may be sent`;

  return (
    <section className="rounded-xl border border-[var(--border)] p-5" aria-label={title}>
      <h3 className="text-[17px] font-medium">{title}</h3>
      <div className="mt-4 flex max-w-lg flex-col gap-4">
        {mode.kind === "add" && (
          <label className="flex flex-col gap-1 text-sm">
            Name
            <input
              className={`${fieldClass} font-mono`}
              value={name}
              placeholder="STRIPE_KEY"
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => setName(e.target.value)}
            />
            <span className="text-xs text-[var(--muted)]">
              How the assistant refers to it. Letters, digits and underscores.
            </span>
          </label>
        )}
        {needsValue && (
          <label className="flex flex-col gap-1 text-sm">
            Value
            <input
              className={fieldClass}
              type="password"
              value={value}
              autoComplete="new-password"
              onChange={(e) => setValue(e.target.value)}
            />
            <span className="text-xs text-[var(--muted)]">
              Kept in this computer's keychain. It can't be read back here once saved.
            </span>
          </label>
        )}
        {needsHosts && (
          <label className="flex flex-col gap-1 text-sm">
            Hosts it may be sent to
            <textarea
              className={`${fieldClass} h-24 py-2 font-mono`}
              value={hosts}
              placeholder={"api.stripe.com\n*.stripe.com"}
              spellCheck={false}
              onChange={(e) => setHosts(e.target.value)}
            />
            <span className="text-xs text-[var(--muted)]">
              One per line. <code>api.example.com</code> for that host, <code>*.example.com</code> for its subdomains (not
              example.com itself). No https:// and no path.
            </span>
          </label>
        )}
        {error && (
          <p role="alert" className="text-sm text-[var(--danger)]">
            {error}
          </p>
        )}
        <div className="flex gap-2">
          <button type="button" className={primaryButtonClass} disabled={!ready || saving} onClick={() => void save()}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button type="button" className={secondaryButtonClass} onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </section>
  );
}

/** Settings > Secrets: keys and tokens the assistant can use in a request without ever seeing them. */
export function SecretsTab({ active }: { active: boolean }) {
  const { data, status, error, retry } = useFetchOnActive(active, getSecrets, EMPTY);
  const [form, setForm] = useState<FormMode | null>(null);
  const [removing, setRemoving] = useState<SecretInfo | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const finished = () => {
    setForm(null);
    retry();
  };

  const confirmRemove = async () => {
    if (!removing) return;
    const target = removing;
    setRemoving(null);
    setActionError(null);
    const result = await deleteSecret(target.name);
    if ("error" in result) setActionError(result.error);
    retry();
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <h2 className="mr-1 text-[22px] font-semibold">Secrets</h2>
        <div className="flex-1" />
        <button
          type="button"
          className={`${primaryButtonClass} flex items-center gap-1.5`}
          disabled={!data.keychain || form !== null}
          onClick={() => setForm({ kind: "add" })}
        >
          <PlusIcon className="h-4 w-4" /> Add secret
        </button>
      </div>

      <div className="rounded-xl bg-[var(--card-bg)] px-5 py-4 text-sm leading-relaxed">
        <p>
          Add a key or token once, then give it to the conversations that need it (right-click a conversation &gt; Edit
          environment). The assistant can use it in a web request only for the hosts you allow here, and never sees its
          value.
        </p>
        <p className="mt-2 text-[var(--muted)]">
          This keeps a value out of what the assistant reads: its messages, tool results and logs. It is not a defence
          against a program that goes looking: anything the assistant runs on this computer runs as you and could read the
          keychain itself, and a host you allow could repeat the value back. Redaction of replies covers common encodings
          only.
        </p>
      </div>

      {!data.keychain && (
        <div role="alert" className="flex gap-3 rounded-xl border border-[var(--danger)] px-5 py-4 text-sm">
          <AlertCircleIcon className="mt-0.5 h-4 w-4 shrink-0 text-[var(--danger)]" />
          <p>
            This computer has no keychain, so a secret can't be saved here: they are never written to a plain file. On
            Windows the keychain is Credential Manager; on Linux it needs a Secret Service such as GNOME Keyring.
          </p>
        </div>
      )}

      {actionError && (
        <p role="alert" className="text-sm text-[var(--danger)]">
          {actionError}
        </p>
      )}

      {form && <SecretForm mode={form} onDone={finished} onCancel={() => setForm(null)} />}

      <FetchRetry status={status} error={error} onRetry={retry} />
      {status === "success" && data.secrets.length === 0 && !form && (
        <p className="py-6 text-center text-sm text-[var(--muted)]">No secrets yet.</p>
      )}
      {data.secrets.length > 0 && (
        <ul className="divide-y divide-[var(--border)]">
          {data.secrets.map((secret) => (
            <li key={secret.name} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3.5">
              <div className="min-w-0 flex-1">
                <div className="font-mono text-[15px]">{secret.name}</div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {secret.hosts.map((host) => (
                    <span key={host} className="rounded-md bg-[var(--card-bg)] px-2 py-0.5 font-mono text-xs">
                      {host}
                    </span>
                  ))}
                  {secret.created_at && (
                    <span className="px-1 py-0.5 text-xs text-[var(--muted)]">Added {shortDate(secret.created_at)}</span>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                <button
                  type="button"
                  className={secondaryButtonClass}
                  disabled={!data.keychain || form !== null}
                  onClick={() => setForm({ kind: "replace", secret })}
                >
                  Replace value
                </button>
                <button type="button" className={secondaryButtonClass} disabled={form !== null} onClick={() => setForm({ kind: "hosts", secret })}>
                  Edit hosts
                </button>
                <button type="button" className={secondaryButtonClass} onClick={() => setRemoving(secret)}>
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {removing && (
        <ConfirmDialog
          title={`Delete ${removing.name}?`}
          description="It is removed from the keychain and from every conversation that was given it. This can't be undone."
          onCancel={() => setRemoving(null)}
          onConfirm={() => void confirmRemove()}
        />
      )}
    </div>
  );
}
