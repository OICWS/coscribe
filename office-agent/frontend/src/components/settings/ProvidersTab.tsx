import { useEffect, useState } from "react";
import { addProvider, getProviders, getProvidersCatalog, removeProvider } from "../../lib/rest";
import type { ProviderCatalogEntry, ProvidersResponse } from "../../types/settings";
import { ConfirmDialog } from "../ConfirmDialog";

/** Mirrors the backend's builtin-provider name list (app.py's BUILTIN_PROVIDERS) --
 * a name matching one of these (case-insensitively) routes through the
 * builtin branch server-side regardless of what base_url was typed. */
const BUILTIN_PROVIDER_NAMES = ["anthropic", "openai", "gemini"];

interface ProvidersTabProps {
  active: boolean;
}

export function ProvidersTab({ active }: ProvidersTabProps) {
  const [catalog, setCatalog] = useState<ProviderCatalogEntry[]>([]);
  const [configured, setConfigured] = useState<ProvidersResponse>({});
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const [baseUrlDisabled, setBaseUrlDisabled] = useState(false);
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null);
  const [removeTarget, setRemoveTarget] = useState<string | null>(null);

  const refresh = () => {
    Promise.all([getProvidersCatalog(), getProviders()]).then(([cat, conf]) => {
      setCatalog(cat);
      setConfigured(conf);
    });
  };

  useEffect(() => {
    if (active) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const prefillFrom = (entry: ProviderCatalogEntry) => {
    setName(entry.name);
    setDefaultModel(entry.default_model || "");
    setApiKey("");
    if (entry.builtin) {
      setBaseUrl("");
      setBaseUrlDisabled(true);
    } else {
      setBaseUrl(entry.base_url);
      setBaseUrlDisabled(false);
    }
    setStatus(null);
  };

  const resetForm = () => {
    setName("");
    setBaseUrl("");
    setApiKey("");
    setDefaultModel("");
    setBaseUrlDisabled(false);
  };

  const submit = async () => {
    const trimmedName = name.trim();
    const isBuiltin = BUILTIN_PROVIDER_NAMES.includes(trimmedName.toLowerCase());
    if (!trimmedName) {
      setStatus({ text: "Name cannot be blank.", error: true });
      return;
    }
    if (!apiKey.trim()) {
      setStatus({ text: "API key cannot be blank.", error: true });
      return;
    }
    if (!isBuiltin && !baseUrl.trim()) {
      setStatus({ text: "Base URL cannot be blank.", error: true });
      return;
    }
    const result = await addProvider(trimmedName, baseUrl, apiKey, defaultModel);
    const rejectedEntries = Object.entries(result.rejected);
    if (rejectedEntries.length > 0) {
      setStatus({ text: `Not added -- ${rejectedEntries[0][1]}`, error: true });
      return;
    }
    setStatus({ text: result.restart_required ? "Added. Restart coscribe-web to apply." : "Added.", error: false });
    resetForm();
    refresh();
  };

  const confirmRemove = async () => {
    if (!removeTarget) return;
    const providerName = removeTarget;
    setRemoveTarget(null);
    await removeProvider(providerName);
    refresh();
  };

  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-[var(--muted)]">
        Anthropic, OpenAI, and Gemini are built in. Any other OpenAI-compatible API can be added as a custom
        provider. Set Default Model on the General tab as <code>name:model</code> once configured here.
      </p>

      <div>
        <h4 className="mb-2 text-sm font-medium">Catalog</h4>
        <div className="grid grid-cols-2 gap-2">
          {catalog.map((entry) => {
            const isConfigured = entry.name in configured;
            return (
              <div key={entry.name} className="flex items-center justify-between rounded-lg border border-[var(--border)] px-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">
                    {entry.name}
                    {isConfigured ? " (configured)" : ""}
                  </div>
                  <div className="truncate text-xs text-[var(--muted)]">{entry.description}</div>
                </div>
                <button
                  type="button"
                  title={isConfigured ? `Update ${entry.name}` : `Add ${entry.name}`}
                  className="ml-2 shrink-0 rounded-md border border-[var(--border)] px-2 py-1 text-sm"
                  onClick={() => prefillFrom(entry)}
                >
                  {isConfigured ? "✓" : "+"}
                </button>
              </div>
            );
          })}
        </div>
      </div>

      <div>
        <h4 className="mb-2 text-sm font-medium">Configured</h4>
        <div className="flex flex-col gap-1">
          {Object.entries(configured).map(([providerName, info]) => (
            <div key={providerName} className="flex items-center justify-between rounded-lg border border-[var(--border)] px-3 py-2 text-sm">
              <div className="min-w-0">
                <div className="font-medium">{providerName}</div>
                <div className="truncate text-xs text-[var(--muted)]">
                  {info.builtin
                    ? `${info.default_model || "no default model set"} -- key ${info.masked_key ?? "no key"}`
                    : `${info.base_url} -- key ${info.masked_key ?? "no key"}`}
                </div>
              </div>
              <button type="button" className="text-[var(--muted)] hover:text-red-500" onClick={() => setRemoveTarget(providerName)}>
                ×
              </button>
            </div>
          ))}
          {Object.keys(configured).length === 0 && <div className="text-sm text-[var(--muted)]">No providers configured.</div>}
        </div>
      </div>

      <div className="flex flex-col gap-2 rounded-lg border border-[var(--border)] p-3">
        <h4 className="text-sm font-medium">Add / update a provider</h4>
        <input
          className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
          placeholder="Name (e.g. deepseek)"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none disabled:opacity-40"
          placeholder={baseUrlDisabled ? "(not needed for built-in providers)" : "Base URL"}
          value={baseUrl}
          disabled={baseUrlDisabled}
          onChange={(e) => setBaseUrl(e.target.value)}
        />
        <input
          type="password"
          className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
          placeholder="API key"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
        <input
          className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
          placeholder="Default model (optional)"
          value={defaultModel}
          onChange={(e) => setDefaultModel(e.target.value)}
        />
        <div className="flex items-center gap-2">
          <button type="button" className="rounded-md bg-[var(--accent)] px-3 py-1 text-sm text-[var(--accent-fg)]" onClick={submit}>
            Save
          </button>
          {status && <span className={`text-sm ${status.error ? "text-red-500" : "text-[var(--muted)]"}`}>{status.text}</span>}
        </div>
      </div>

      {removeTarget && (
        <ConfirmDialog
          title="Remove provider?"
          description={`"${removeTarget}" and its stored API key will be removed. Any thread or workflow still using it will fail until you add it again.`}
          onCancel={() => setRemoveTarget(null)}
          onConfirm={confirmRemove}
        />
      )}
    </div>
  );
}
