import { type ComponentType, type SVGProps, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  addMcpServer,
  bumpMcpVersion,
  getMcpCatalog,
  getMcpServers,
  getNpmLatestVersion,
  reconnectMcpServer,
  removeMcpServer,
  setConnectorToolPolicies,
} from "../../lib/rest";
import type {
  ConnectorTool,
  ConnectorToolPolicy,
  McpCatalogEntry,
  McpServerInfo,
  McpServersResponse,
} from "../../types/settings";
import {
  ArrowLeftIcon,
  BanIcon,
  CheckCircleIcon,
  CheckIcon,
  ChevronDownIcon,
  CloseIcon,
  HandIcon,
  MoreIcon,
  PlusIcon,
  SearchIcon,
} from "../icons";
import { FetchRetry } from "./FetchRetry";
import { fieldClass, primaryButtonClass, secondaryButtonClass } from "./SettingRow";
import { useClickOutside } from "../../lib/useClickOutside";
import { useFetchOnActive } from "../../lib/useFetchOnActive";

/** Module-level: a first `npx` install can take a minute or more, and the
 * add keeps running after Settings closes or the tab changes, so its
 * "connecting" marker has to outlive this component. */
const pendingConnectorAdds = new Set<string>();
let listeners: (() => void)[] = [];
function notifyPendingChanged() {
  for (const listener of listeners) listener();
}

const PINNED_NPM_PACKAGE_RE = /^((?:@[^/@]+\/)?[^@]+)@(\d[\w.-]*)$/;
function findPinnedNpmPackage(args: string[]): { name: string; version: string } | null {
  for (const arg of args) {
    const match = PINNED_NPM_PACKAGE_RE.exec(arg);
    if (match) return { name: match[1], version: match[2] };
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

type LocalServerArgs = { command: string; args: string[]; env?: Record<string, string> };
type RemoteServerArgs = { server_url: string; headers?: Record<string, string> };

/** A catalog entry, a connector someone added, or both. */
interface ConnectorRow {
  name: string;
  title: string;
  catalogEntry: McpCatalogEntry | null;
  serverInfo: McpServerInfo | null;
}

function buildRows(catalog: McpCatalogEntry[], servers: McpServersResponse): ConnectorRow[] {
  const rows: ConnectorRow[] = catalog.map((entry) => ({
    name: entry.name,
    title: entry.title ?? entry.name,
    catalogEntry: entry,
    serverInfo: servers[entry.name] ?? null,
  }));
  for (const [name, info] of Object.entries(servers)) {
    if (!catalog.some((entry) => entry.name === name)) {
      rows.push({ name, title: name, catalogEntry: null, serverInfo: info });
    }
  }
  return rows;
}

function ConnectorBadge({ title, large }: { title: string; large?: boolean }) {
  return (
    <span
      className={`flex shrink-0 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--field-bg)] font-medium text-[var(--muted)] ${
        large ? "h-11 w-11 text-base" : "h-7 w-7 text-xs"
      }`}
    >
      {title.slice(0, 1).toUpperCase()}
    </span>
  );
}

const POLICIES: {
  value: ConnectorToolPolicy;
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
}[] = [
  { value: "allow", label: "Always allow", icon: CheckCircleIcon },
  { value: "ask", label: "Needs approval", icon: HandIcon },
  { value: "block", label: "Blocked", icon: BanIcon },
];

/** Three icon buttons, one per policy, for a single tool. */
function PolicyToggle({
  tool,
  onChange,
}: {
  tool: ConnectorTool;
  onChange: (policy: ConnectorToolPolicy) => void;
}) {
  return (
    <div role="radiogroup" aria-label={`${tool.title} permission`} className="flex shrink-0 rounded-lg bg-[var(--card-bg)] p-0.5">
      {POLICIES.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={tool.policy === value}
          aria-label={label}
          title={label}
          className={`flex h-7 w-8 items-center justify-center rounded-md ${
            tool.policy === value
              ? "bg-[var(--bg)] text-[var(--fg)] shadow-sm ring-1 ring-[var(--border)]"
              : "text-[var(--muted)] hover:text-[var(--fg)]"
          }`}
          onClick={() => onChange(value)}
        >
          <Icon className="h-4 w-4" />
        </button>
      ))}
    </div>
  );
}

/** Sets every tool in a group at once; shows the group's policy when its
 * tools agree. */
function GroupPolicyMenu({
  tools,
  onChange,
}: {
  tools: ConnectorTool[];
  onChange: (policy: ConnectorToolPolicy) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(ref, () => setOpen(false), open);
  const shared = tools.every((t) => t.policy === tools[0]?.policy) ? tools[0]?.policy : undefined;
  const current = POLICIES.find((p) => p.value === shared);
  const Icon = current?.icon;
  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex h-9 items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--field-bg)] px-3 text-sm hover:bg-[var(--card-bg)]"
        onClick={() => setOpen((v) => !v)}
      >
        {Icon && <Icon className="h-4 w-4" />}
        {current?.label ?? "Custom"}
        <ChevronDownIcon className="h-3.5 w-3.5 text-[var(--muted)]" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-20 mt-1 min-w-44 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] py-1 shadow-[var(--shadow)]"
        >
          {POLICIES.map(({ value, label, icon: ItemIcon }) => (
            <button
              key={value}
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]"
              onClick={() => {
                setOpen(false);
                onChange(value);
              }}
            >
              <ItemIcon className="h-4 w-4" />
              <span className="flex-1">{label}</span>
              {shared === value && <CheckIcon className="h-3.5 w-3.5" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ToolGroup({
  title,
  tools,
  onChange,
}: {
  title: string;
  tools: ConnectorTool[];
  onChange: (policies: Record<string, ConnectorToolPolicy>) => void;
}) {
  const [open, setOpen] = useState(true);
  if (tools.length === 0) return null;
  return (
    <div className="flex flex-col">
      <div className="flex items-center gap-3 py-2">
        <button
          type="button"
          aria-expanded={open}
          className="flex flex-1 items-center gap-2 text-left text-[15px]"
          onClick={() => setOpen((v) => !v)}
        >
          <ChevronDownIcon className={`h-4 w-4 text-[var(--muted)] transition-transform ${open ? "" : "-rotate-90"}`} />
          {title}
          <span className="rounded-md bg-[var(--card-bg)] px-1.5 text-xs text-[var(--muted)]">{tools.length}</span>
        </button>
        <GroupPolicyMenu
          tools={tools}
          onChange={(policy) => onChange(Object.fromEntries(tools.map((t) => [t.name, policy])))}
        />
      </div>
      {open && (
        <div className="flex flex-col divide-y divide-[var(--border)] pl-6">
          {tools.map((tool) => (
            <div key={tool.name} className="flex items-center gap-6 py-3">
              <div className="min-w-0 flex-1">
                <div className="text-sm">{tool.title}</div>
                {tool.description && tool.description !== tool.title && (
                  <div className="mt-0.5 line-clamp-2 text-[13px] text-[var(--muted)]">{tool.description}</div>
                )}
              </div>
              <PolicyToggle tool={tool} onChange={(policy) => onChange({ [tool.name]: policy })} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ConnectorMenu({
  pinned,
  onReconnect,
  onCheckUpdate,
}: {
  pinned: boolean;
  onReconnect: () => void;
  onCheckUpdate: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(ref, () => setOpen(false), open);
  const item = "flex w-full px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]";
  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-label="More connector actions"
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--border)] text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
        onClick={() => setOpen((v) => !v)}
      >
        <MoreIcon className="h-4 w-4" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-20 mt-1 min-w-44 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] py-1 shadow-[var(--shadow)]"
        >
          <button
            type="button"
            role="menuitem"
            className={item}
            onClick={() => {
              setOpen(false);
              onReconnect();
            }}
          >
            Reconnect
          </button>
          {pinned && (
            <button
              type="button"
              role="menuitem"
              className={item}
              onClick={() => {
                setOpen(false);
                onCheckUpdate();
              }}
            >
              Check for updates
            </button>
          )}
        </div>
      )}
    </div>
  );
}

interface Notice {
  text: string;
  error: boolean;
  update?: { pkg: string; version: string };
}

/** An added connector: its tools and what each may do, and Disconnect. */
function ConnectorDetail({
  row,
  pending,
  notice,
  onBack,
  onDisconnect,
  onReconnect,
  onCheckUpdate,
  onApplyUpdate,
  onPolicies,
}: {
  row: ConnectorRow;
  pending: boolean;
  notice: Notice | null;
  onBack: () => void;
  onDisconnect: () => void;
  onReconnect: () => void;
  onCheckUpdate: (pkg: string) => void;
  onApplyUpdate: (pkg: string, version: string) => void;
  onPolicies: (policies: Record<string, ConnectorToolPolicy>) => void;
}) {
  const info = row.serverInfo!;
  const pinned = findPinnedNpmPackage(info.args ?? []);
  const source = info.server_url ?? `${info.command ?? ""} ${(info.args ?? []).join(" ")}`.trim();
  const tools = info.tools ?? [];
  return (
    <div className="flex flex-col gap-5">
      <button type="button" className="flex w-fit items-center gap-2 text-[15px] text-[var(--fg)] hover:opacity-70" onClick={onBack}>
        <ArrowLeftIcon className="h-4 w-4" /> Your connectors
      </button>
      <div className="flex items-center gap-4">
        <ConnectorBadge title={row.title} large />
        <div className="min-w-0 flex-1 truncate text-lg font-semibold">{row.title}</div>
        <button type="button" className={secondaryButtonClass} onClick={onDisconnect}>
          Disconnect
        </button>
        <ConnectorMenu
          pinned={pinned !== null}
          onReconnect={onReconnect}
          onCheckUpdate={() => pinned && onCheckUpdate(pinned.name)}
        />
      </div>
      <div className="flex flex-col gap-1">
        {row.catalogEntry && <p className="text-[15px] leading-relaxed">{row.catalogEntry.description}</p>}
        <p className="truncate font-mono text-xs text-[var(--muted)]" title={source}>
          {source}
        </p>
      </div>

      {notice && (
        <p role="status" className={`text-sm ${notice.error ? "text-[var(--danger)]" : "text-[var(--muted)]"}`}>
          {notice.text}{" "}
          {notice.update && (
            <button
              type="button"
              className="text-[var(--accent)] hover:underline"
              onClick={() => onApplyUpdate(notice.update!.pkg, notice.update!.version)}
            >
              Update
            </button>
          )}
        </p>
      )}

      {!info.connected ? (
        <div className="flex items-center gap-3 rounded-xl border border-[var(--border)] px-4 py-3 text-sm">
          <span className="flex-1 text-[var(--muted)]">
            {pending ? "Connecting…" : "Not connected. Its tools show up here once it connects."}
          </span>
          <button type="button" className={secondaryButtonClass} disabled={pending} onClick={onReconnect}>
            Retry
          </button>
        </div>
      ) : (
        <section className="flex flex-col">
          <h3 className="text-[15px] font-semibold">Tool permissions</h3>
          <p className="mt-1 text-sm text-[var(--muted)]">
            Choose when coscribe is allowed to use these tools. Needs approval follows the conversation's own mode.
          </p>
          <div className="mt-3 flex flex-col divide-y divide-[var(--border)]">
            <ToolGroup title="Read-only tools" tools={tools.filter((t) => t.read_only)} onChange={onPolicies} />
            <ToolGroup title="Write/delete tools" tools={tools.filter((t) => !t.read_only)} onChange={onPolicies} />
          </div>
        </section>
      )}
    </div>
  );
}

/** A catalog connector not added yet: what it is, and Connect. */
function CatalogConnectorPage({
  entry,
  pending,
  notice,
  onBack,
  onConnect,
}: {
  entry: McpCatalogEntry;
  pending: boolean;
  notice: Notice | null;
  onBack: () => void;
  onConnect: () => void;
}) {
  const title = entry.title ?? entry.name;
  const pkg = findPinnedNpmPackage(entry.args);
  return (
    <div className="flex flex-col gap-5">
      <button type="button" className="flex w-fit items-center gap-2 text-[15px] text-[var(--fg)] hover:opacity-70" onClick={onBack}>
        <ArrowLeftIcon className="h-4 w-4" /> Your connectors
      </button>
      <div className="flex items-center gap-4 rounded-xl bg-[var(--card-bg)] px-5 py-5">
        <ConnectorBadge title={title} large />
        <div className="min-w-0 flex-1">
          <div className="text-xl font-semibold">{title}</div>
        </div>
        <button
          type="button"
          disabled={pending}
          className={`${primaryButtonClass} ${pending ? "running-wave-ring" : ""}`}
          onClick={onConnect}
        >
          {pending ? "Connecting…" : "Connect"}
        </button>
      </div>
      <p className="text-[15px] leading-relaxed">{entry.description}</p>
      {notice && (
        <p role="status" className={`text-sm ${notice.error ? "text-[var(--danger)]" : "text-[var(--muted)]"}`}>
          {notice.text}
        </p>
      )}
      <div className="grid grid-cols-2 gap-6 border-t border-[var(--border)] pt-5 text-sm">
        {entry.made_by && (
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-[var(--muted)]">Made by</div>
            {entry.homepage ? (
              <a href={entry.homepage} target="_blank" rel="noreferrer" className="mt-1 block text-[var(--accent)] hover:underline">
                {entry.made_by}
              </a>
            ) : (
              <div className="mt-1">{entry.made_by}</div>
            )}
          </div>
        )}
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
            {pkg ? "Package" : "Command"}
          </div>
          <div className="mt-1 font-mono text-[13px]">
            {pkg ? `${pkg.name} ${pkg.version}` : `${entry.command} ${entry.args.join(" ")}`}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Name plus an MCP server URL, or a local command for a server that runs
 * on this computer. */
function AddCustomConnectorDialog({
  existing,
  onClose,
  onAdd,
}: {
  existing: string[];
  onClose: () => void;
  onAdd: (name: string, server: LocalServerArgs | RemoteServerArgs) => void;
}) {
  const [local, setLocal] = useState(false);
  const [name, setName] = useState("");
  const [serverUrl, setServerUrl] = useState("");
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("");
  const [env, setEnv] = useState("");

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const trimmedName = name.trim();
  const nameTaken = existing.includes(trimmedName);
  const urlOk = /^https?:\/\/\S+$/i.test(serverUrl.trim());
  const ready = trimmedName !== "" && !nameTaken && (local ? command.trim() !== "" : urlOk);

  const submit = () => {
    if (!ready) return;
    onAdd(
      trimmedName,
      local
        ? { command: command.trim(), args: args.split(/\s+/).filter(Boolean), env: parseEnvLines(env) }
        : { server_url: serverUrl.trim() },
    );
  };

  const hint = "mt-1.5 text-[13px] text-[var(--muted)]";
  return createPortal(
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-connector-title"
        className="flex w-[min(640px,100vw-2rem)] flex-col gap-5 rounded-2xl border border-[var(--border)] bg-[var(--panel-bg)] p-7 shadow-[var(--shadow)]"
      >
        <div className="flex items-start gap-4">
          <h2 id="add-connector-title" className="flex-1 text-2xl font-semibold">
            Add custom connector
          </h2>
          <button
            type="button"
            aria-label="Close"
            className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
            onClick={onClose}
          >
            <CloseIcon className="h-4 w-4" />
          </button>
        </div>
        <p className="-mt-2 text-[15px] text-[var(--muted)]">Connect coscribe to your data and tools.</p>

        <div>
          <input
            aria-label="Name"
            autoFocus
            className={`${fieldClass} h-10 w-full`}
            placeholder="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <p className={hint}>{nameTaken ? "A connector already has this name." : "Shown in the connectors list."}</p>
        </div>

        {local ? (
          <div className="flex flex-col gap-3">
            <input
              aria-label="Command"
              className={`${fieldClass} h-10 w-full font-mono text-[13px]`}
              placeholder="Command, e.g. npx"
              value={command}
              onChange={(e) => setCommand(e.target.value)}
            />
            <input
              aria-label="Arguments"
              className={`${fieldClass} h-10 w-full font-mono text-[13px]`}
              placeholder="Arguments, separated by spaces"
              value={args}
              onChange={(e) => setArgs(e.target.value)}
            />
            <textarea
              aria-label="Environment variables"
              className={`${fieldClass} h-auto min-h-20 w-full py-2 font-mono text-[13px]`}
              placeholder="Environment variables, one per line: KEY=value"
              value={env}
              onChange={(e) => setEnv(e.target.value)}
            />
            <p className="text-[13px] text-[var(--muted)]">It runs as a program on this computer, with your permissions.</p>
          </div>
        ) : (
          <div>
            <input
              aria-label="MCP server URL"
              className={`${fieldClass} h-10 w-full`}
              placeholder="MCP server URL"
              value={serverUrl}
              onChange={(e) => setServerUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
            <p className={hint}>
              The HTTPS address where the server accepts MCP requests, for example https://mcp.example.com/mcp.
            </p>
          </div>
        )}
        <button
          type="button"
          className="-mt-2 w-fit text-sm text-[var(--accent)] hover:underline"
          onClick={() => setLocal((v) => !v)}
        >
          {local ? "Use a server URL instead" : "Run a local command instead"}
        </button>

        <p className="text-[15px] text-[var(--muted)]">
          Only use connectors from developers you trust. coscribe can't check what their tools do or whether they
          change.
        </p>

        <div className="flex justify-end gap-2">
          <button type="button" className={secondaryButtonClass} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className={primaryButtonClass} disabled={!ready} onClick={submit}>
            Continue
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function StatusCell({ row, pending, onConnect }: { row: ConnectorRow; pending: boolean; onConnect: () => void }) {
  if (pending) {
    return <span className="running-wave-ring rounded-md px-2.5 py-1 text-sm">Connecting…</span>;
  }
  if (row.serverInfo?.connected) return <CheckIcon aria-label="Connected" className="h-4 w-4" />;
  if (row.serverInfo) return <span className="text-sm text-[var(--danger)]">Not connected</span>;
  return (
    <button
      type="button"
      className={secondaryButtonClass}
      onClick={(e) => {
        e.stopPropagation();
        onConnect();
      }}
    >
      Connect
    </button>
  );
}

const EMPTY: { catalog: McpCatalogEntry[]; servers: McpServersResponse } = { catalog: [], servers: {} };

/** Settings > Connectors: the list, a connector's own page (its tools and
 * their permissions), and adding a custom one. */
export function ConnectorsTab({ active }: { active: boolean }) {
  const [, forceRender] = useState(0);
  const [openName, setOpenName] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const addMenuRef = useRef<HTMLDivElement>(null);
  useClickOutside(addMenuRef, () => setAddMenuOpen(false), addMenuOpen);
  const [search, setSearch] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);

  useEffect(() => {
    const listener = () => forceRender((n) => n + 1);
    listeners.push(listener);
    return () => {
      listeners = listeners.filter((l) => l !== listener);
    };
  }, []);

  const {
    data: { catalog, servers },
    status,
    retry: refresh,
  } = useFetchOnActive(
    active,
    () => Promise.all([getMcpCatalog(), getMcpServers()]).then(([cat, srv]) => ({ catalog: cat, servers: srv })),
    EMPTY,
  );

  // A slow server can finish connecting well after the tab opened, and
  // Settings has no push channel, so its live state is polled.
  const [liveServers, setLiveServers] = useState<McpServersResponse | null>(null);
  const pollLiveServers = () => {
    getMcpServers()
      .then(setLiveServers)
      .catch(() => {});
  };
  useEffect(() => {
    if (!active) return;
    setLiveServers(null);
    pollLiveServers();
    const id = setInterval(pollLiveServers, 4000);
    return () => clearInterval(id);
  }, [active]);
  const effectiveServers = liveServers ?? servers;

  const withPending = async (name: string, work: () => Promise<void>) => {
    pendingConnectorAdds.add(name);
    notifyPendingChanged();
    try {
      await work();
    } finally {
      pendingConnectorAdds.delete(name);
      notifyPendingChanged();
      refresh();
      pollLiveServers();
    }
  };

  const performAdd = (name: string, server: LocalServerArgs | RemoteServerArgs) =>
    withPending(name, async () => {
      setNotice({ text: "Connecting -- a first install can take a minute or more.", error: false });
      const result = await addMcpServer(name, server);
      const rejected = Object.values(result.rejected);
      if (rejected.length > 0) setNotice({ text: `Not added -- ${rejected[0]}`, error: true });
      else if (result.connected) setNotice(null);
      else setNotice({ text: `Saved, but couldn't connect${result.error ? ` -- ${result.error}` : "."}`, error: true });
    });

  const connectCatalog = (entry: McpCatalogEntry) => {
    setOpenName(entry.name);
    if (entry.needs_config) {
      setNotice({ text: `${entry.title ?? entry.name} needs its own token -- add it as a custom connector.`, error: false });
      return;
    }
    void performAdd(entry.name, { command: entry.command, args: entry.args });
  };

  const disconnect = async (name: string) => {
    await removeMcpServer(name);
    setOpenName(null);
    setNotice(null);
    refresh();
    pollLiveServers();
  };

  const reconnect = (name: string) =>
    withPending(name, async () => {
      const result = await reconnectMcpServer(name);
      setNotice(result.connected ? null : { text: `Still couldn't connect${result.error ? ` -- ${result.error}` : "."}`, error: true });
    });

  const checkUpdate = async (pkg: string, current: string | undefined) => {
    const result = await getNpmLatestVersion(pkg);
    if ("error" in result) setNotice({ text: `Couldn't check for updates: ${result.error}`, error: true });
    else if (result.latest === current) setNotice({ text: `Up to date (${current}).`, error: false });
    else setNotice({ text: `Version ${result.latest} is available.`, error: false, update: { pkg, version: result.latest } });
  };

  const applyUpdate = (name: string, pkg: string, version: string) =>
    withPending(name, async () => {
      const result = await bumpMcpVersion(name, pkg, version);
      setNotice(
        result.connected
          ? { text: `Updated to ${version}.`, error: false }
          : { text: `Updated, but couldn't reconnect${result.error ? ` -- ${result.error}` : "."}`, error: true },
      );
    });

  const setPolicies = async (name: string, policies: Record<string, ConnectorToolPolicy>) => {
    setLiveServers((current) => {
      const base = current ?? servers;
      const info = base[name];
      if (!info?.tools) return current;
      const tools = info.tools.map((t) => (t.name in policies ? { ...t, policy: policies[t.name] } : t));
      return { ...base, [name]: { ...info, tools } };
    });
    try {
      await setConnectorToolPolicies(name, policies);
    } catch {
      setNotice({ text: "Couldn't save the permission -- try again.", error: true });
    }
    pollLiveServers();
  };

  const rows = buildRows(catalog, effectiveServers);
  const opened = openName ? rows.find((r) => r.name === openName) : undefined;
  const backToList = () => {
    setOpenName(null);
    setNotice(null);
  };

  if (opened?.serverInfo) {
    const pinned = findPinnedNpmPackage(opened.serverInfo.args ?? []);
    return (
      <ConnectorDetail
        row={opened}
        pending={pendingConnectorAdds.has(opened.name)}
        notice={notice}
        onBack={backToList}
        onDisconnect={() => void disconnect(opened.name)}
        onReconnect={() => void reconnect(opened.name)}
        onCheckUpdate={(pkg) => void checkUpdate(pkg, pinned?.version)}
        onApplyUpdate={(pkg, version) => void applyUpdate(opened.name, pkg, version)}
        onPolicies={(policies) => void setPolicies(opened.name, policies)}
      />
    );
  }
  if (opened?.catalogEntry) {
    return (
      <CatalogConnectorPage
        entry={opened.catalogEntry}
        pending={pendingConnectorAdds.has(opened.name)}
        notice={notice}
        onBack={backToList}
        onConnect={() => connectCatalog(opened.catalogEntry!)}
      />
    );
  }

  const q = search.trim().toLowerCase();
  const visible = q ? rows.filter((r) => `${r.title} ${r.name}`.toLowerCase().includes(q)) : rows;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="mr-1 text-[22px] font-semibold">Connectors</h2>
        <div className="flex-1" />
        <div className="flex h-9 w-60 items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--field-bg)] px-3 focus-within:border-[var(--focus)] focus-within:ring-2 focus-within:ring-[var(--focus)]/15">
          <SearchIcon className="h-4 w-4 shrink-0 text-[var(--muted)]" />
          <input
            aria-label="Search connectors"
            className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--muted)]"
            placeholder="Search connectors"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="relative" ref={addMenuRef}>
          <button
            type="button"
            aria-haspopup="menu"
            aria-expanded={addMenuOpen}
            className="flex h-9 items-center gap-1.5 rounded-lg bg-[var(--primary)] px-3.5 text-sm font-medium text-[var(--primary-fg)] hover:bg-[var(--primary-hover)]"
            onClick={() => setAddMenuOpen((v) => !v)}
          >
            <PlusIcon className="h-4 w-4" /> Add <ChevronDownIcon className="h-3.5 w-3.5" />
          </button>
          {addMenuOpen && (
            <div
              role="menu"
              className="absolute right-0 top-full z-20 mt-1 min-w-52 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] py-1 shadow-[var(--shadow)]"
            >
              <button
                type="button"
                role="menuitem"
                className="flex w-full px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]"
                onClick={() => {
                  setAddMenuOpen(false);
                  setAdding(true);
                }}
              >
                Add custom connector
              </button>
            </div>
          )}
        </div>
      </div>

      {notice && (
        <p role="status" className={`text-sm ${notice.error ? "text-[var(--danger)]" : "text-[var(--muted)]"}`}>
          {notice.text}
        </p>
      )}

      <FetchRetry status={status} onRetry={refresh} />
      {status === "success" && visible.length === 0 && (
        <p className="text-sm text-[var(--muted)]">{q ? "No connectors match." : "No connectors yet."}</p>
      )}
      {status === "success" && visible.length > 0 && (
        <table className="w-full table-fixed text-left text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-[13px] text-[var(--muted)]">
              <th className="w-[58%] pb-2 pr-4 font-normal">Connector</th>
              <th className="w-[22%] pb-2 pr-4 font-normal">Type</th>
              <th className="w-[20%] pb-2 font-normal">Status</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr
                key={row.name}
                tabIndex={0}
                className="cursor-pointer border-b border-[var(--border)] hover:bg-[var(--card-bg)] focus-visible:bg-[var(--card-bg)] focus-visible:outline-none"
                onClick={() => {
                  setNotice(null);
                  setOpenName(row.name);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") setOpenName(row.name);
                }}
              >
                <td className="py-3 pr-4">
                  <div className="flex min-w-0 items-center gap-3">
                    <ConnectorBadge title={row.title} />
                    <span className="truncate text-[15px]">{row.title}</span>
                  </div>
                </td>
                <td className="py-3 pr-4 text-[15px]">
                  {row.serverInfo?.server_url !== undefined ? "Web" : "Local"}
                  {!row.catalogEntry && (
                    <span className="ml-2 rounded-md bg-[var(--card-bg)] px-1.5 py-0.5 text-xs text-[var(--muted)]">Custom</span>
                  )}
                </td>
                <td className="py-3">
                  <StatusCell
                    row={row}
                    pending={pendingConnectorAdds.has(row.name)}
                    onConnect={() => row.catalogEntry && connectCatalog(row.catalogEntry)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {adding && (
        <AddCustomConnectorDialog
          existing={rows.map((r) => r.name)}
          onClose={() => setAdding(false)}
          onAdd={(name, server) => {
            setAdding(false);
            setOpenName(name);
            void performAdd(name, server);
          }}
        />
      )}
    </div>
  );
}
