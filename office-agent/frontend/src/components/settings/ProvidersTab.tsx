import { useState } from "react";
import { addProvider, getProviders, getProvidersCatalog, removeProvider } from "../../lib/rest";
import { useFetchOnActive } from "../../lib/useFetchOnActive";
import type { ProviderCatalogEntry, ProvidersResponse } from "../../types/settings";
import { ConfirmDialog } from "../ConfirmDialog";
import { FetchRetry } from "./FetchRetry";
import { SettingRows, SettingsSection, fieldClass, primaryButtonClass, secondaryButtonClass } from "./SettingRow";

/** Mirrors the backend's builtin-provider name list (app.py's BUILTIN_PROVIDERS) --
 * a name matching one of these (case-insensitively) routes through the
 * builtin branch server-side regardless of what base_url was typed. */
const BUILTIN_PROVIDER_NAMES = ["anthropic", "openai", "gemini"];

const EMPTY: { catalog: ProviderCatalogEntry[]; configured: ProvidersResponse } = { catalog: [], configured: {} };

const loadProviders = () =>
  Promise.all([getProvidersCatalog(), getProviders()]).then(([catalog, configured]) => ({ catalog, configured }));

interface ProvidersTabProps {
  active: boolean;
}

export function ProvidersTab({ active }: ProvidersTabProps) {
  const { data, status: loadStatus, error: loadError, retry: refresh } = useFetchOnActive(active, loadProviders, EMPTY);
  const { catalog, configured } = data;
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const [baseUrlDisabled, setBaseUrlDisabled] = useState(false);
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null);
  const [removeTarget, setRemoveTarget] = useState<string | null>(null);

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
    let result;
    try {
      result = await addProvider(trimmedName, baseUrl, apiKey, defaultModel);
    } catch (err) {
      setStatus({ text: err instanceof Error ? err.message : "Not added.", error: true });
      return;
    }
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
    try {
      await removeProvider(providerName);
    } catch (err) {
      setStatus({ text: err instanceof Error ? err.message : `Couldn't remove ${providerName}.`, error: true });
    }
    refresh();
  };

  const detail = (info: (typeof configured)[string]) =>
    info.builtin
      ? `${info.default_model || "No default model"} · key ${info.masked_key ?? "not set"}`
      : `${info.base_url} · key ${info.masked_key ?? "not set"}`;

  return (
    <div className="flex flex-col gap-10">
      <FetchRetry status={loadStatus} error={loadError} onRetry={refresh} />

      <SettingsSection title="Your providers" description="The services coscribe's models come from. Pick the default one on the General page.">
        {loadStatus === "success" && Object.keys(configured).length === 0 ? (
          <p className="py-3 text-sm text-[var(--muted)]">No providers yet -- add one below.</p>
        ) : (
          <SettingRows>
            {Object.entries(configured).map(([providerName, info]) => (
              <div key={providerName} className="flex items-center justify-between gap-4 py-3.5">
                <div className="min-w-0">
                  <div className="text-[15px]">{providerName}</div>
                  <div className="truncate text-[13px] text-[var(--muted)]">{detail(info)}</div>
                </div>
                <button type="button" className={secondaryButtonClass} onClick={() => setRemoveTarget(providerName)}>
                  Remove
                </button>
              </div>
            ))}
          </SettingRows>
        )}
      </SettingsSection>

      <SettingsSection
        title="Add a provider"
        description="Anthropic, OpenAI and Gemini are built in; any other OpenAI-compatible service can be added. Pick one to fill in the form, or enter it yourself."
      >
        <div className="mt-3 grid grid-cols-2 gap-2">
          {catalog.map((entry) => {
            const isConfigured = entry.name in configured;
            return (
              <button
                key={entry.name}
                type="button"
                title={isConfigured ? `Update ${entry.name}` : `Add ${entry.name}`}
                className="flex items-center justify-between gap-3 rounded-xl border border-[var(--border)] bg-[var(--field-bg)] px-3.5 py-2.5 text-left hover:border-[var(--border-hover)]"
                onClick={() => prefillFrom(entry)}
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{entry.name}</div>
                  <div className="truncate text-[13px] text-[var(--muted)]">{entry.description}</div>
                </div>
                <span className={`shrink-0 text-[13px] ${isConfigured ? "text-[var(--muted)]" : "text-[var(--fg)]"}`}>
                  {isConfigured ? "Added" : "Add"}
                </span>
              </button>
            );
          })}
        </div>

        <div className="mt-5 grid grid-cols-2 gap-x-3 gap-y-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] text-[var(--muted)]">Name</span>
            <input className={fieldClass} placeholder="e.g. deepseek" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] text-[var(--muted)]">Base URL</span>
            <input
              className={`${fieldClass} disabled:opacity-40`}
              placeholder={baseUrlDisabled ? "Not needed for built-in providers" : "https://api.example.com/v1"}
              value={baseUrl}
              disabled={baseUrlDisabled}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] text-[var(--muted)]">API key</span>
            <input type="password" className={fieldClass} value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] text-[var(--muted)]">Default model (optional)</span>
            <input
              className={fieldClass}
              placeholder="e.g. deepseek-chat"
              value={defaultModel}
              onChange={(e) => setDefaultModel(e.target.value)}
            />
          </label>
        </div>
        <div className="mt-4 flex items-center justify-end gap-3">
          {status && <span className={`mr-auto text-sm ${status.error ? "text-red-500" : "text-[var(--muted)]"}`}>{status.text}</span>}
          <button type="button" className={primaryButtonClass} onClick={submit}>
            Save provider
          </button>
        </div>
      </SettingsSection>

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
