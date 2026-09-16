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
import type { McpCatalogEntry, McpServerInfo, McpServersResponse } from "../../types/settings";
import { ArrowLeftIcon, CheckIcon, SearchIcon, TrashIcon } from "../icons";
import { FetchRetry } from "./FetchRetry";
import { useFetchOnActive } from "../../lib/useFetchOnActive";

const CONNECTOR_ICONS: Record<string, string> = {
  playwright: "🎭",
  fetch: "🌐",
  memory: "🧠",
  "sequential-thinking": "🧩",
  time: "🕐",
  slack: "💬",
  office365: "📧",
};

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

type ConnectorType = "local" | "remote";

/** One row of the unified table below -- either a catalog entry (may or
 * may not be added yet), a hand-added custom entry with no catalog match,
 * or both merged when a catalog entry has been added. Coscribe has no
 * second, bigger "Discover" universe beyond MCP_CATALOG's ~7 entries (see
 * ConnectorsTab's own docstring), so catalog + added servers merge into
 * one list instead of Skills' two-tab split. */
interface ConnectorRow {
  name: string;
  description: string | null;
  type: ConnectorType;
  isAdded: boolean;
  connected: boolean;
  catalogEntry: McpCatalogEntry | null;
  serverInfo: McpServerInfo | null;
}

function buildRows(catalog: McpCatalogEntry[], servers: McpServersResponse): ConnectorRow[] {
  const rows: ConnectorRow[] = [];
  const seen = new Set<string>();
  for (const entry of catalog) {
    const info = servers[entry.name] ?? null;
    rows.push({
      name: entry.name,
      description: entry.description,
      type: "local", // every catalog entry is a local stdio process today
      isAdded: info !== null,
      connected: info?.connected ?? false,
      catalogEntry: entry,
      serverInfo: info,
    });
    seen.add(entry.name);
  }
  for (const [name, info] of Object.entries(servers)) {
    if (seen.has(name)) continue;
    rows.push({
      name,
      description: null,
      type: info.server_url !== undefined ? "remote" : "local",
      isAdded: true,
      connected: info.connected,
      catalogEntry: null,
      serverInfo: info,
    });
  }
  return rows;
}

type LocalServerArgs = { command: string; args: string[]; env?: Record<string, string> };
type RemoteServerArgs = { server_url: string; headers?: Record<string, string> };

interface ConnectorRowViewProps {
  row: ConnectorRow;
  isPending: boolean;
  updateCheck: string | undefined;
  onConnect: () => void;
  onRemove: () => void;
  onCheckUpdate: (pkg: string) => void;
  onApplyUpdate: (pkg: string, version: string) => void;
}

/** One <tr> in the unified table -- top-level, not nested in
 * ConnectorsTab, per this repo's own "no inner components" convention. */
function ConnectorRowView({ row, isPending, updateCheck, onConnect, onRemove, onCheckUpdate, onApplyUpdate }: ConnectorRowViewProps) {
  const pinned = row.type === "local" ? findPinnedNpmPackage(row.serverInfo?.args ?? []) : null;
  const latest = updateCheck?.startsWith("latest:") ? updateCheck.slice(7) : null;
  const detail =
    row.serverInfo === null
      ? row.description
      : row.serverInfo.server_url !== undefined
        ? row.serverInfo.server_url
        : `${row.serverInfo.command ?? ""} ${(row.serverInfo.args ?? []).join(" ")}`.trim();

  return (
    <tr className="border-b border-[var(--border)] last:border-b-0">
      <td className="min-w-0 overflow-hidden py-2.5 pr-4">
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0">{CONNECTOR_ICONS[row.name] ?? "🔌"}</span>
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">{row.name}</div>
            {detail && <div className="truncate text-xs text-[var(--muted)]">{detail}</div>}
            {pinned && (
              <div className="mt-0.5 flex items-center gap-2 text-xs">
                {!updateCheck && (
                  <button type="button" className="text-[var(--accent)]" onClick={() => onCheckUpdate(pinned.name)}>
                    Check for updates
                  </button>
                )}
                {updateCheck && !latest && <span className="text-[var(--muted)]">{updateCheck}</span>}
                {latest && latest === pinned.version && <span className="text-[var(--muted)]">Up to date (v{pinned.version}).</span>}
                {latest && latest !== pinned.version && (
                  <>
                    <span className="text-[var(--muted)]">
                      v{pinned.version} -&gt; v{latest} available.
                    </span>
                    <button type="button" className="text-[var(--accent)]" onClick={() => onApplyUpdate(pinned.name, latest)}>
                      Update
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </td>
      <td className="py-2.5 pr-4 text-sm capitalize text-[var(--muted)]">{row.type}</td>
      <td className="py-2.5 pr-4">
        {row.isAdded ? (
          row.connected ? (
            <CheckIcon className="h-4 w-4 text-[var(--accent)]" />
          ) : (
            <span className="text-sm text-[var(--muted)]">Not connected</span>
          )
        ) : (
          <button
            type="button"
            disabled={isPending}
            className="rounded-md border border-[var(--border)] px-2.5 py-1 text-sm disabled:opacity-60"
            onClick={onConnect}
          >
            {isPending ? "Connecting…" : "Connect"}
          </button>
        )}
      </td>
      <td className="w-8 py-2.5 text-right">
        {row.isAdded && (
          <button type="button" aria-label={`Remove ${row.name}`} className="text-[var(--muted)] hover:text-red-500" onClick={onRemove}>
            <TrashIcon className="h-3.5 w-3.5" />
          </button>
        )}
      </td>
    </tr>
  );
}

interface AddConnectorViewProps {
  catalog: McpCatalogEntry[];
  servers: McpServersResponse;
  pendingConnectorAdds: Set<string>;
  onBack: () => void;
  onCatalogAdd: (entry: McpCatalogEntry) => void;
  onManualAdd: (name: string, server: LocalServerArgs | RemoteServerArgs) => void;
}

/** Settings > Connectors > Add -- catalog picks (one click) plus a manual
 * Local/Remote form, top-level per the "no inner components" convention. */
function AddConnectorView({ catalog, servers, pendingConnectorAdds, onBack, onCatalogAdd, onManualAdd }: AddConnectorViewProps) {
  const [manualType, setManualType] = useState<"local" | "remote">("local");
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("");
  const [env, setEnv] = useState("");
  const [serverUrl, setServerUrl] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const submit = () => {
    const trimmedName = name.trim();
    if (manualType === "remote") {
      const trimmedUrl = serverUrl.trim();
      if (!trimmedName || !trimmedUrl) {
        setFormError("Name and MCP Server URL are required.");
        return;
      }
      onManualAdd(trimmedName, { server_url: trimmedUrl });
    } else {
      const trimmedCommand = command.trim();
      if (!trimmedName || !trimmedCommand) {
        setFormError("Name and command are required.");
        return;
      }
      const argList = args.split(/\s+/).filter((a) => a.length > 0);
      onManualAdd(trimmedName, { command: trimmedCommand, args: argList, env: parseEnvLines(env) });
    }
    setFormError(null);
    setName("");
    setCommand("");
    setArgs("");
    setEnv("");
    setServerUrl("");
  };

  return (
    <div className="flex flex-col gap-4">
      <button type="button" className="flex w-fit items-center gap-1.5 text-sm text-[var(--muted)] hover:text-[var(--fg)]" onClick={onBack}>
        <ArrowLeftIcon className="h-3.5 w-3.5" /> Connectors
      </button>
      <h2 className="text-lg font-semibold">Add connector</h2>

      <div>
        <div className="mb-2 text-sm font-medium">From the catalog</div>
        <div className="flex flex-col gap-1.5">
          {catalog.map((entry) => {
            const isAdded = entry.name in servers;
            const isPending = pendingConnectorAdds.has(entry.name);
            return (
              <div key={entry.name} className="flex items-center justify-between gap-3 rounded-lg border border-[var(--border)] px-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">
                    {CONNECTOR_ICONS[entry.name] ?? "🔌"} {entry.name}
                  </div>
                  <div className="truncate text-xs text-[var(--muted)]">{entry.description}</div>
                </div>
                <button
                  type="button"
                  disabled={isAdded || isPending}
                  className="shrink-0 rounded-md border border-[var(--border)] px-2.5 py-1 text-sm disabled:opacity-60"
                  onClick={() => onCatalogAdd(entry)}
                >
                  {isAdded ? "Added" : isPending ? "Connecting…" : "Add"}
                </button>
              </div>
            );
          })}
        </div>
      </div>

      <div className="flex flex-col gap-2 rounded-lg border border-[var(--border)] p-3">
        <h4 className="text-sm font-medium">Add manually</h4>
        <div className="flex gap-1 rounded-lg bg-[var(--card-bg)] p-1">
          {(["local", "remote"] as const).map((t) => (
            <button
              key={t}
              type="button"
              className={`flex-1 rounded-md py-1 text-sm font-medium capitalize ${
                manualType === t ? "bg-[var(--accent)] text-[var(--accent-fg)]" : "text-[var(--muted)] hover:text-[var(--fg)]"
              }`}
              onClick={() => setManualType(t)}
            >
              {t}
            </button>
          ))}
        </div>
        <input
          className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        {manualType === "remote" ? (
          <input
            className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
            placeholder="MCP Server URL (https://...)"
            value={serverUrl}
            onChange={(e) => setServerUrl(e.target.value)}
          />
        ) : (
          <>
            <input
              className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
              placeholder="Command (e.g. npx)"
              value={command}
              onChange={(e) => setCommand(e.target.value)}
            />
            <input
              className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
              placeholder="Args (space-separated)"
              value={args}
              onChange={(e) => setArgs(e.target.value)}
            />
            <textarea
              className="rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none"
              placeholder="Env vars, one per line: KEY=value"
              rows={3}
              value={env}
              onChange={(e) => setEnv(e.target.value)}
            />
          </>
        )}
        <div className="flex items-center gap-3">
          <button type="button" className="self-start rounded-md bg-[var(--accent)] px-3 py-1 text-sm text-[var(--accent-fg)]" onClick={submit}>
            Add
          </button>
          {formError && <span className="text-sm text-red-500">{formError}</span>}
        </div>
      </div>
    </div>
  );
}

interface ConnectorsTabProps {
  active: boolean;
}

const EMPTY_CATALOG_AND_SERVERS: { catalog: McpCatalogEntry[]; servers: McpServersResponse } = {
  catalog: [],
  servers: {},
};

/** Settings > Connectors, restructured toward docs/ui-references/
 * connectors-tab.png: catalog ("Built-in", one-click add) and hand-added
 * servers ("Custom") used to be two separate tabs -- they merge into one
 * searchable/filterable table here, since MCP_CATALOG's ~7 entries
 * (web/app.py) already ARE the full known set coscribe ships, unlike
 * Claude's own "Your connectors" vs a much bigger "Discover" marketplace
 * -- there's no second universe to discover beyond it, so no Discover tab
 * or "Popular for X" recommendation strip either (no coscribe data behind
 * either). Status now carries a real, live `connected` field
 * (web/app.py's mcp_connections registry), not just "configured" --
 * added-but-not-currently-connected shows as "Not connected" rather than
 * a fake retryable "Connect" (env/headers secrets are masked on read, so
 * there's no way to actually replay a failed connect from this tab; the
 * honest fix is Remove + re-add). Add's manual form now supports Remote
 * (server_url) too, not just Local (command/args/env) -- the validation
 * layer (tools/mcp.py's validate_mcp_config) already handled server_url,
 * only the POST /api/mcp/servers payload shape and this form were
 * missing it. */
export function ConnectorsTab({ active }: ConnectorsTabProps) {
  const [, forceRender] = useState(0);
  const [view, setView] = useState<"list" | "add">("list");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "connected" | "not-connected">("all");
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null);
  const [browserPrompt, setBrowserPrompt] = useState<McpCatalogEntry | null>(null);
  const [updateChecks, setUpdateChecks] = useState<Record<string, string>>({});
  const [configPath, setConfigPath] = useState<string | null | undefined>(undefined);

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

  const performAdd = async (name: string, server: LocalServerArgs | RemoteServerArgs) => {
    pendingConnectorAdds.add(name);
    notifyPendingChanged();
    setStatus({ text: "Adding -- first-run installs can take a minute or more.", error: false });
    try {
      const result = await addMcpServer(name, server);
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

  const addWithBrowserCheck = async (entry: McpCatalogEntry) => {
    const check = await checkBrowser();
    if (!check.checked) {
      await performAdd(entry.name, { command: entry.command, args: entry.args });
      return;
    }
    if (check.path) {
      await performAdd(entry.name, { command: entry.command, args: [...entry.args, "--executable-path", check.path] });
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
    await performAdd(entry.name, { command: entry.command, args: entry.args });
  };

  const onCatalogAdd = (entry: McpCatalogEntry) => {
    if (entry.needs_config) {
      // No prefill sub-step needed anymore -- the manual form already
      // lives on this same Add view; the user just fills it in with the
      // catalog entry's own required env keys as a hint via the
      // description above it. Simplest correct thing given needs_config
      // only applies to one entry (slack) today.
      setStatus({ text: `${entry.name} needs its own token -- fill in "Add manually" below with the required env vars.`, error: false });
      return;
    }
    if (entry.needs_browser_check) {
      addWithBrowserCheck(entry);
      return;
    }
    performAdd(entry.name, { command: entry.command, args: entry.args });
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

  if (view === "add") {
    return (
      <AddConnectorView
        catalog={catalog}
        servers={servers}
        pendingConnectorAdds={pendingConnectorAdds}
        onBack={() => setView("list")}
        onCatalogAdd={(entry) => {
          onCatalogAdd(entry);
        }}
        onManualAdd={(name, server) => {
          performAdd(name, server);
          setView("list");
        }}
      />
    );
  }

  let rows = buildRows(catalog, servers);
  const q = search.trim().toLowerCase();
  if (q) rows = rows.filter((r) => r.name.toLowerCase().includes(q));
  if (filter === "connected") rows = rows.filter((r) => r.isAdded && r.connected);
  if (filter === "not-connected") rows = rows.filter((r) => !r.isAdded || !r.connected);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">Connectors</h2>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-2 rounded-md border border-[var(--border)] px-2.5 py-1.5">
            <SearchIcon className="h-3.5 w-3.5 shrink-0 text-[var(--muted)]" />
            <input
              className="w-40 min-w-0 bg-transparent text-sm outline-none placeholder:text-[var(--muted)]"
              placeholder="Search connectors"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <button
            type="button"
            className="rounded-md bg-[var(--fg)] px-3 py-1.5 text-sm font-medium text-[var(--bg)]"
            onClick={() => setView("add")}
          >
            Add
          </button>
        </div>
      </div>

      <p className="text-xs text-[var(--muted)]">
        MCP servers run local commands or talk to a remote MCP endpoint -- only add ones you trust. Add/remove/update
        applies immediately, no restart needed -- open conversations pick up the change too.
      </p>
      {configPath !== undefined && (
        <p className="truncate text-xs text-[var(--muted)]">
          Config file: {configPath ?? "not created yet -- will be written to ./mcp.json on first add"}
        </p>
      )}

      <div className="flex gap-1">
        {(["all", "connected", "not-connected"] as const).map((f) => (
          <button
            key={f}
            type="button"
            className={`rounded-md border px-2.5 py-1 text-sm capitalize ${
              filter === f ? "border-[var(--fg)] font-medium" : "border-[var(--border)] text-[var(--muted)] hover:text-[var(--fg)]"
            }`}
            onClick={() => setFilter(f)}
          >
            {f === "not-connected" ? "Not connected" : f}
          </button>
        ))}
      </div>

      <FetchRetry status={catalogStatus} onRetry={refresh} />

      {catalogStatus === "success" && rows.length === 0 && <div className="text-sm text-[var(--muted)]">No connectors match.</div>}
      {catalogStatus === "success" && rows.length > 0 && (
        <table className="w-full table-fixed text-left text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-xs text-[var(--muted)]">
              <th className="w-[55%] pb-2 pr-4 font-medium">Connector</th>
              <th className="w-[15%] pb-2 pr-4 font-medium">Type</th>
              <th className="w-[20%] pb-2 pr-4 font-medium">Status</th>
              <th className="w-8 pb-2"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <ConnectorRowView
                key={row.name}
                row={row}
                isPending={pendingConnectorAdds.has(row.name)}
                updateCheck={updateChecks[row.name]}
                onConnect={() => row.catalogEntry && onCatalogAdd(row.catalogEntry)}
                onRemove={() => remove(row.name)}
                onCheckUpdate={(pkg) => checkUpdate(row.name, pkg)}
                onApplyUpdate={(pkg, version) => applyUpdate(row.name, pkg, version)}
              />
            ))}
          </tbody>
        </table>
      )}

      {browserPrompt && (
        <div className="rounded-lg border border-[var(--accent)] p-3 text-sm">
          <div className="mb-2">Chromium isn't installed yet -- {browserPrompt.name} needs it to run.</div>
          <div className="flex gap-2">
            <button type="button" className="rounded-md bg-[var(--accent)] px-3 py-1 text-sm text-[var(--accent-fg)]" onClick={() => installBrowserThenAdd(browserPrompt)}>
              Install Chromium
            </button>
            <button type="button" className="rounded-md border border-[var(--border)] px-3 py-1 text-sm" onClick={() => setBrowserPrompt(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {status && <span className={`text-sm ${status.error ? "text-red-500" : "text-[var(--muted)]"}`}>{status.text}</span>}
    </div>
  );
}
