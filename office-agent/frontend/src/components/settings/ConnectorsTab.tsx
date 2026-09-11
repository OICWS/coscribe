import { useEffect, useState } from "react";
import {
  addMcpServer,
  bumpMcpVersion,
  checkBrowser,
  getConfig,
  getMcpCatalog,
  getMcpServers,
  getNpmLatestVersion,
  installBrowser,
  removeMcpServer,
} from "../../lib/rest";
import type { McpCatalogEntry, McpServersResponse } from "../../types/settings";
import { FetchRetry } from "./FetchRetry";
import { useFetchOnActive } from "../../lib/useFetchOnActive";

const CONNECTOR_ICONS: Record<string, string> = { playwright: "🎭", fetch: "🌐" };

/** Module-level, not component state -- survives ConnectorsTab unmounting
 * when the settings modal closes or the category switches away. A slow
 * first `npx` install can take a minute+; the add's fetch keeps running
 * regardless of whether this component is mounted, so the pending marker
 * has to live outside it too (mirrors app.js's own module-level
 * pendingConnectorAdds, called out there for the same reason). */
const pendingConnectorAdds = new Set<string>();
let listeners: (() => void)[] = [];
function notifyPendingChanged() {
  for (const listener of listeners) listener();
}

const PINNED_NPM_PACKAGE_RE = /^((?:@[^/@]+\/)?[^@]+)@(\d[\w.-]*)$/;
function findPinnedNpmPackage(args: string[]): { index: number; name: string; version: string } | null {
  for (let i = 0; i < args.length; i += 1) {
    const match = PINNED_NPM_PACKAGE_RE.exec(args[i]);
    if (match) return { index: i, name: match[1], version: match[2] };
  }
  return null;
}

function parseEnvLines(text: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const idx = line.indexOf("=");
    if (idx <= 0) continue;
    env[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
  }
  return env;
}

interface ConnectorsTabProps {
  active: boolean;
}

const EMPTY_CATALOG_AND_SERVERS: { catalog: McpCatalogEntry[]; servers: McpServersResponse } = {
  catalog: [],
  servers: {},
};

export function ConnectorsTab({ active }: ConnectorsTabProps) {
  const [, forceRender] = useState(0);
  const [subTab, setSubTab] = useState<"builtin" | "custom">("builtin");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "not-added" | "added">("all");
  const [sort, setSort] = useState<"name-asc" | "name-desc">("name-asc");
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null);
  const [browserPrompt, setBrowserPrompt] = useState<McpCatalogEntry | null>(null);
  const [updateChecks, setUpdateChecks] = useState<Record<string, string>>({});
  const [configPath, setConfigPath] = useState<string | null | undefined>(undefined);

  const [customName, setCustomName] = useState("");
  const [customCommand, setCustomCommand] = useState("");
  const [customArgs, setCustomArgs] = useState("");
  const [customEnv, setCustomEnv] = useState("");

  useEffect(() => {
    const listener = () => forceRender((n) => n + 1);
    listeners.push(listener);
    return () => {
      listeners = listeners.filter((l) => l !== listener);
    };
  }, []);

  // catalogStatus/refresh replace this tab's own former bare `getX().then
  // (setX)` (no .catch, no loading state) -- see useFetchOnActive's own
  // docstring for the real, live-hit bug that left this tab permanently
  // showing "No connectors match" (indistinguishable from a genuinely
  // empty result) after one transient failure on a fresh Windows install.
  const {
    data: { catalog, servers },
    status: catalogStatus,
    retry: refresh,
  } = useFetchOnActive(
    active,
    () => Promise.all([getMcpCatalog(), getMcpServers()]).then(([cat, srv]) => ({ catalog: cat, servers: srv })),
    EMPTY_CATALOG_AND_SERVERS,
  );

  useEffect(() => {
    if (!active) return;
    // mcp.json's real on-disk path -- answers "where does an added
    // connector's config actually live", not shown anywhere else in this
    // tab. null means no connector has been added yet (the file is only
    // created on first add, see add_mcp_server); it then defaults to
    // ./mcp.json next to wherever coscribe was started. Re-fires on every
    // re-activation (unlike the catalog/servers fetch above, this one
    // isn't gated to "only once") -- cheap, and self-heals from a
    // transient failure the next time the tab opens; .catch is just
    // silencing an unhandled-rejection warning, configPath staying
    // undefined either way (its own "not shown" case already renders
    // fine, see below).
    getConfig()
      .then((cfg) => setConfigPath(cfg.COSCRIBE_MCP_CONFIG_PATH))
      .catch(() => {});
  }, [active]);

  const performAdd = async (name: string, command: string, args: string[], env: Record<string, string> = {}) => {
    pendingConnectorAdds.add(name);
    notifyPendingChanged();
    setStatus({ text: "Adding -- first-run installs can take a minute or more.", error: false });
    try {
      const result = await addMcpServer(name, command, args, env);
      const rejectedEntries = Object.entries(result.rejected);
      if (rejectedEntries.length > 0) {
        setStatus({ text: `Not added -- ${rejectedEntries[0][1]}`, error: true });
      } else {
        setStatus({
          text: result.connected ? "Added." : "Saved, but couldn't connect -- check the command/args.",
          error: !result.connected,
        });
      }
    } finally {
      pendingConnectorAdds.delete(name);
      notifyPendingChanged();
      refresh();
    }
  };

  const prefillCustomForm = (entry: McpCatalogEntry) => {
    setSubTab("custom");
    setCustomName(entry.name);
    setCustomCommand(entry.command);
    setCustomArgs(entry.args.join(" "));
    setCustomEnv(Object.entries(entry.env ?? {}).map(([k, v]) => `${k}=${v}`).join("\n"));
  };

  const addWithBrowserCheck = async (entry: McpCatalogEntry) => {
    const check = await checkBrowser();
    if (!check.checked) {
      await performAdd(entry.name, entry.command, entry.args);
      return;
    }
    if (check.path) {
      await performAdd(entry.name, entry.command, [...entry.args, "--executable-path", check.path]);
      return;
    }
    setBrowserPrompt(entry);
  };

  const installBrowserThenAdd = async (entry: McpCatalogEntry) => {
    setBrowserPrompt(null);
    setStatus({ text: "Installing Chromium -- this can take a while.", error: false });
    const result = await installBrowser();
    if (!result.success) {
      setStatus({ text: `Install failed -- ${result.error ?? "unknown error"}`, error: true });
      return;
    }
    await performAdd(entry.name, entry.command, entry.args);
  };

  const onCatalogAddClick = (entry: McpCatalogEntry) => {
    if (entry.needs_config) {
      prefillCustomForm(entry);
      return;
    }
    if (entry.needs_browser_check) {
      addWithBrowserCheck(entry);
      return;
    }
    performAdd(entry.name, entry.command, entry.args);
  };

  const remove = async (name: string) => {
    await removeMcpServer(name);
    refresh();
  };

  const checkUpdate = async (name: string, pkg: string) => {
    const result = await getNpmLatestVersion(pkg);
    if ("error" in result) {
      setUpdateChecks({ ...updateChecks, [name]: `Couldn't check: ${result.error}` });
      return;
    }
    setUpdateChecks({ ...updateChecks, [name]: `latest:${result.latest}` });
  };

  const applyUpdate = async (name: string, pkg: string, version: string) => {
    await bumpMcpVersion(name, pkg, version);
    const next = { ...updateChecks };
    delete next[name];
    setUpdateChecks(next);
    setStatus({ text: "Updated.", error: false });
    refresh();
  };

  const submitCustom = () => {
    const trimmedName = customName.trim();
    const trimmedCommand = customCommand.trim();
    if (!trimmedName || !trimmedCommand) {
      setStatus({ text: "Name and command are required.", error: true });
      return;
    }
    const args = customArgs.split(/\s+/).filter((a) => a.length > 0);
    const env = parseEnvLines(customEnv);
    performAdd(trimmedName, trimmedCommand, args, env);
    setCustomName("");
    setCustomCommand("");
    setCustomArgs("");
    setCustomEnv("");
  };

  let filtered = catalog.filter((entry) => entry.name.toLowerCase().includes(search.toLowerCase()));
  if (filter === "not-added") filtered = filtered.filter((entry) => !(entry.name in servers));
  if (filter === "added") filtered = filtered.filter((entry) => entry.name in servers);
  filtered = [...filtered].sort((a, b) => (sort === "name-asc" ? a.name.localeCompare(b.name) : b.name.localeCompare(a.name)));

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-[var(--muted)]">
        MCP servers run local commands -- only add ones you trust. Add/remove/update applies immediately, no
        restart needed -- open conversations pick up the change too.
      </p>
      {configPath !== undefined && (
        <p className="truncate text-xs text-[var(--muted)]">
          Config file: {configPath ?? "not created yet -- will be written to ./mcp.json on first add"}
        </p>
      )}

      <div className="flex gap-1 border-b border-[var(--border)]">
        {(["builtin", "custom"] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            className={`px-3 py-1.5 text-sm ${subTab === tab ? "border-b-2 border-[var(--accent)] font-medium" : "text-[var(--muted)]"}`}
            onClick={() => setSubTab(tab)}
          >
            {tab === "builtin" ? "Built-in" : "Custom"}
          </button>
        ))}
      </div>

      {subTab === "builtin" && (
        <>
          <div className="flex gap-2">
            <input
              className="flex-1 rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
              placeholder="Search connectors..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select
              className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm"
              value={filter}
              onChange={(e) => setFilter(e.target.value as typeof filter)}
            >
              <option value="all">All</option>
              <option value="not-added">Not added</option>
              <option value="added">Added</option>
            </select>
            <select
              className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm"
              value={sort}
              onChange={(e) => setSort(e.target.value as typeof sort)}
            >
              <option value="name-asc">Name A-Z</option>
              <option value="name-desc">Name Z-A</option>
            </select>
          </div>

          <FetchRetry status={catalogStatus} onRetry={refresh} />

          <div className="grid grid-cols-2 gap-2">
            {filtered.map((entry) => {
              const isPending = pendingConnectorAdds.has(entry.name);
              const isAdded = entry.name in servers;
              return (
                <div key={entry.name} className="flex items-center justify-between rounded-lg border border-[var(--border)] px-3 py-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">
                      {CONNECTOR_ICONS[entry.name] ?? "🔌"} {entry.name}
                    </div>
                    <div className="truncate text-xs text-[var(--muted)]">{entry.description}</div>
                  </div>
                  <button
                    type="button"
                    disabled={isPending || isAdded}
                    className="ml-2 shrink-0 rounded-md border border-[var(--border)] px-2 py-1 text-sm disabled:opacity-60"
                    onClick={() => onCatalogAddClick(entry)}
                  >
                    {isPending ? "…" : isAdded ? "✓" : "+"}
                  </button>
                </div>
              );
            })}
            {catalogStatus === "success" && filtered.length === 0 && (
              <div className="col-span-2 text-sm text-[var(--muted)]">No connectors match.</div>
            )}
          </div>

          {browserPrompt && (
            <div className="rounded-lg border border-[var(--accent)] p-3 text-sm">
              <div className="mb-2">Chromium isn't installed yet -- {browserPrompt.name} needs it to run.</div>
              <div className="flex gap-2">
                <button
                  type="button"
                  className="rounded-md bg-[var(--accent)] px-3 py-1 text-sm text-[var(--accent-fg)]"
                  onClick={() => installBrowserThenAdd(browserPrompt)}
                >
                  Install Chromium
                </button>
                <button
                  type="button"
                  className="rounded-md border border-[var(--border)] px-3 py-1 text-sm"
                  onClick={() => setBrowserPrompt(null)}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

        </>
      )}

      {subTab === "custom" && (
        <>
          <div className="flex flex-col gap-1">
            {Object.entries(servers).map(([name, info]) => {
              const isRemote = info.server_url !== undefined;
              const pinned = isRemote ? null : findPinnedNpmPackage(info.args ?? []);
              const checkResult = updateChecks[name];
              const latest = checkResult?.startsWith("latest:") ? checkResult.slice(7) : null;
              return (
                <div key={name} className="rounded-lg border border-[var(--border)] px-3 py-2 text-sm">
                  <div className="flex items-center justify-between">
                    <div className="min-w-0">
                      <div className="font-medium">{name}</div>
                      {isRemote ? (
                        <>
                          <div className="break-all text-xs text-[var(--muted)]">{info.server_url}</div>
                          {info.masked_headers && Object.keys(info.masked_headers).length > 0 && (
                            <div className="break-all text-xs text-[var(--muted)]">
                              {Object.entries(info.masked_headers).map(([k, v]) => `${k}: ${v}`).join("  ")}
                            </div>
                          )}
                        </>
                      ) : (
                        <>
                          <div className="break-all text-xs text-[var(--muted)]">
                            {info.command} {(info.args ?? []).join(" ")}
                          </div>
                          {info.masked_env && Object.keys(info.masked_env).length > 0 && (
                            <div className="break-all text-xs text-[var(--muted)]">
                              {Object.entries(info.masked_env).map(([k, v]) => `${k}=${v}`).join("  ")}
                            </div>
                          )}
                        </>
                      )}
                      {pendingConnectorAdds.has(name) && <div className="text-xs text-[var(--muted)]">Connecting...</div>}
                    </div>
                    <button type="button" className="text-[var(--muted)] hover:text-red-500" onClick={() => remove(name)}>
                      ×
                    </button>
                  </div>
                  {pinned && (
                    <div className="mt-1 flex items-center gap-2 text-xs">
                      {!checkResult && (
                        <button type="button" className="text-[var(--accent)]" onClick={() => checkUpdate(name, pinned.name)}>
                          Check for updates
                        </button>
                      )}
                      {checkResult && !latest && <span>{checkResult}</span>}
                      {latest && latest === pinned.version && <span>Up to date (v{pinned.version}).</span>}
                      {latest && latest !== pinned.version && (
                        <>
                          <span>
                            v{pinned.version} -&gt; v{latest} available.
                          </span>
                          <button
                            type="button"
                            className="text-[var(--accent)]"
                            onClick={() => applyUpdate(name, pinned.name, latest)}
                          >
                            Update
                          </button>
                        </>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            {Object.keys(servers).length === 0 && <div className="text-sm text-[var(--muted)]">No custom connectors configured.</div>}
          </div>

          <div className="flex flex-col gap-2 rounded-lg border border-[var(--border)] p-3">
            <h4 className="text-sm font-medium">Add a custom MCP server</h4>
            <input
              className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
              placeholder="Name"
              value={customName}
              onChange={(e) => setCustomName(e.target.value)}
            />
            <input
              className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
              placeholder="Command (e.g. npx)"
              value={customCommand}
              onChange={(e) => setCustomCommand(e.target.value)}
            />
            <input
              className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
              placeholder="Args (space-separated)"
              value={customArgs}
              onChange={(e) => setCustomArgs(e.target.value)}
            />
            <textarea
              className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
              placeholder="Env vars, one per line: KEY=value"
              rows={3}
              value={customEnv}
              onChange={(e) => setCustomEnv(e.target.value)}
            />
            <button type="button" className="self-start rounded-md bg-[var(--accent)] px-3 py-1 text-sm text-[var(--accent-fg)]" onClick={submitCustom}>
              Add
            </button>
          </div>
        </>
      )}

      {status && <span className={`text-sm ${status.error ? "text-red-500" : "text-[var(--muted)]"}`}>{status.text}</span>}
    </div>
  );
}
