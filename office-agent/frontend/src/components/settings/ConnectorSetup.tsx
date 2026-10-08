import { useRef, useState } from "react";
import { exportMcpOAuthApp, importMcpOAuthApps, removeMcpOAuthApp, saveMcpOAuthApp } from "../../lib/rest";
import type { McpCatalogEntry, McpSetup } from "../../types/settings";
import { adminMessage } from "../../lib/connectorSetup";
import { CheckIcon, CopyIcon } from "../icons";
import { fieldClass, primaryButtonClass, secondaryButtonClass } from "./SettingRow";

function CopyBox({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard
      .writeText(value)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {});
  };
  return (
    <div className="mt-2 flex items-start gap-2 rounded-lg border border-[var(--border)] bg-[var(--field-bg)] px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="text-xs text-[var(--muted)]">{label}</div>
        <div className="mt-0.5 whitespace-pre-wrap break-all font-mono text-[13px]">{value}</div>
      </div>
      <button
        type="button"
        className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
        onClick={copy}
      >
        {copied ? <CheckIcon className="h-3.5 w-3.5" /> : <CopyIcon className="h-3.5 w-3.5" />}
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

/**
 * The one-time setup for a service that needs an app registered with it:
 * the steps, the credentials form and, when already saved, a line to change
 * or share it. A person on a team can import what an admin exported and skip
 * straight to signing in.
 */
export function ConnectorSetup({
  entry,
  setup,
  saved,
  redirectUri,
  onChanged,
}: {
  entry: McpCatalogEntry;
  setup: McpSetup;
  saved: boolean;
  redirectUri: string;
  onChanged: () => void;
}) {
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [changing, setChanging] = useState(false);
  const [adminCopied, setAdminCopied] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const title = entry.title ?? entry.name;

  const save = async () => {
    setBusy(true);
    setError(null);
    const result = await saveMcpOAuthApp(setup.group, clientId.trim(), secret.trim());
    setBusy(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    setClientId("");
    setSecret("");
    setChanging(false);
    onChanged();
  };

  const exportSetup = async () => {
    setError(null);
    try {
      const data = await exportMcpOAuthApp(setup.group);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `coscribe-${setup.group}-setup.json`;
      link.click();
      URL.revokeObjectURL(link.href);
      setMessage("Saved the file. It holds the app's secret -- share it only with people who should have it.");
    } catch {
      setError("Couldn't export the setup.");
    }
  };

  const importFile = async (file: File) => {
    setError(null);
    const result = await importMcpOAuthApps(await file.text());
    if ("error" in result) setError(result.error);
    else {
      setMessage(null);
      onChanged();
    }
  };

  const copyAdminMessage = () => {
    void navigator.clipboard
      .writeText(adminMessage(entry, setup, redirectUri))
      .then(() => {
        setAdminCopied(true);
        setTimeout(() => setAdminCopied(false), 1500);
      })
      .catch(() => {});
  };

  const form = (
    <div className="mt-3 flex max-w-md flex-col gap-3">
      <label className="flex flex-col gap-1 text-sm">
        Client ID
        <input
          className={fieldClass}
          value={clientId}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => setClientId(e.target.value)}
        />
      </label>
      {setup.needs_secret && (
        <label className="flex flex-col gap-1 text-sm">
          Client secret
          <input
            className={fieldClass}
            type="password"
            value={secret}
            autoComplete="off"
            onChange={(e) => setSecret(e.target.value)}
          />
        </label>
      )}
      <div className="flex items-center gap-2">
        <button type="button" disabled={busy || !clientId.trim()} className={primaryButtonClass} onClick={() => void save()}>
          {busy ? "Saving…" : "Save"}
        </button>
        {changing && (
          <button type="button" className={secondaryButtonClass} onClick={() => setChanging(false)}>
            Cancel
          </button>
        )}
      </div>
    </div>
  );

  const importButton = (
    <>
      <input
        ref={fileRef}
        type="file"
        accept="application/json,.json"
        className="hidden"
        aria-label="Setup file"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void importFile(file);
          e.target.value = "";
        }}
      />
      <button type="button" className="text-sm text-[var(--accent)] hover:underline" onClick={() => fileRef.current?.click()}>
        Import a setup file
      </button>
    </>
  );

  if (saved && !changing) {
    return (
      <section className="rounded-xl border border-[var(--border)] px-5 py-4" aria-label={`${title} setup`}>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <span className="flex items-center gap-1.5">
            <CheckIcon className="h-4 w-4" /> {setup.provider} app saved
          </span>
          <button type="button" className="text-[var(--accent)] hover:underline" onClick={() => setChanging(true)}>
            Change credentials
          </button>
          <button type="button" className="text-[var(--accent)] hover:underline" onClick={() => void exportSetup()}>
            Export setup
          </button>
          <button
            type="button"
            className="text-[var(--accent)] hover:underline"
            onClick={() => void removeMcpOAuthApp(setup.group).then(onChanged)}
          >
            Remove app
          </button>
        </div>
        {message && <p className="mt-2 text-xs text-[var(--muted)]">{message}</p>}
        {error && (
          <p role="alert" className="mt-2 text-sm text-[var(--danger)]">
            {error}
          </p>
        )}
      </section>
    );
  }

  return (
    <section id={`setup-${entry.name}`} className="rounded-xl border border-[var(--border)] px-5 py-5" aria-label={`${title} setup`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-lg font-semibold">Set up {title}</h3>
        <span className="text-xs text-[var(--muted)]">One time only</span>
      </div>
      <p className="mt-1 text-sm text-[var(--muted)]">
        {setup.provider} doesn't let programs register themselves, so you create a small app there and give coscribe its
        credentials. Nothing else leaves your computer.
      </p>
      <ol className="mt-4 flex flex-col gap-5">
        {setup.steps.map((step, index) => (
          <li key={step.title} className="flex gap-3">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--card-bg)] text-xs font-medium">
              {index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[15px] font-medium">{step.title}</div>
              <p className="mt-0.5 text-sm text-[var(--muted)]">{step.body}</p>
              {step.link && (
                <a
                  href={step.link.url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 inline-block rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--card-bg)]"
                >
                  {step.link.label} ↗
                </a>
              )}
              {step.redirect && <CopyBox label="Redirect address" value={redirectUri} />}
              {step.copy?.map((item) => (
                <CopyBox key={item.label} label={item.label} value={item.value} />
              ))}
            </div>
          </li>
        ))}
        <li className="flex gap-3">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--card-bg)] text-xs font-medium">
            {setup.steps.length + 1}
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[15px] font-medium">Paste what {setup.provider} gives you</div>
            {form}
          </div>
        </li>
      </ol>
      {setup.notes && <p className="mt-4 text-xs text-[var(--muted)]">{setup.notes}</p>}
      {error && (
        <p role="alert" className="mt-3 text-sm text-[var(--danger)]">
          {error}
        </p>
      )}
      <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-[var(--border)] pt-3 text-sm">
        <span className="text-[var(--muted)]">Someone already set this up for your team?</span>
        {importButton}
        <button type="button" className="text-[var(--accent)] hover:underline" onClick={copyAdminMessage}>
          {adminCopied ? "Copied" : "Copy a message for your admin"}
        </button>
      </div>
    </section>
  );
}
