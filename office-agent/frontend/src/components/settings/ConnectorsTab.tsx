import { type ComponentType, type SVGProps, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  addMcpServer,
  bumpMcpVersion,
  getMcpCatalog,
  getMcpOAuthApps,
  getMcpServers,
  getNpmLatestVersion,
  getSecrets,
  reconnectMcpServer,
  removeMcpServer,
  setConnectorToolPolicies,
  signInMcpServer,
} from "../../lib/rest";
import type {
  ConnectorTool,
  ConnectorToolPolicy,
  McpCatalogEntry,
  McpOAuthApps,
  McpServerInfo,
  McpServersResponse,
  SecretInfo,
} from "../../types/settings";
import {
  ArrowLeftIcon,
  BanIcon,
  CheckCircleIcon,
  CheckIcon,
  ChevronDownIcon,
  CloseIcon,
  ExternalLinkIcon,
  HandIcon,
  MoreIcon,
  PlusIcon,
  SearchIcon,
} from "../icons";
import { ConnectorIcon } from "./ConnectorIcon";
import { FetchRetry } from "./FetchRetry";
import { friendlySignInError } from "../../lib/connectorSetup";
import { ConnectorSetup } from "./ConnectorSetup";
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

function parseHeaderLines(text: string): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const idx = line.indexOf(":");
    if (idx > 0) headers[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
  }
  return headers;
}

const SECRET_PLACEHOLDER = /\{\{secret:([A-Za-z_][A-Za-z0-9_]{0,63})\}\}/g;

// Mirrors runtime/secret_store.py's host_allowed: "*.b.com" covers subdomains, not b.com.
function hostAllowed(pattern: string, host: string): boolean {
  const lower = host.toLowerCase();
  return pattern.startsWith("*.") ? lower.endsWith(pattern.slice(1)) && lower !== pattern.slice(2) : lower === pattern;
}

/** What is wrong with the secrets `text` refers to, in the server's own terms:
 * one that doesn't exist, or (for a header, which goes to `host`) one not allowed
 * for that host. The server checks again when the connector is added. */
function secretProblems(text: string, secrets: SecretInfo[], host: string | null): string[] {
  const problems: string[] = [];
  for (const match of text.matchAll(SECRET_PLACEHOLDER)) {
    const secret = secrets.find((s) => s.name === match[1]);
    if (!secret) problems.push(`There is no secret named ${match[1]}.`);
    else if (host && !secret.hosts.some((pattern) => hostAllowed(pattern, host))) {
      problems.push(`${match[1]} may only be sent to ${secret.hosts.join(", ")}, not ${host}.`);
    }
  }
  return problems;
}

/** Puts `{{secret:NAME}}` into the text field it sits under, at the caret. */
function SecretPicker({
  secrets,
  onPick,
}: {
  secrets: SecretInfo[];
  onPick: (placeholder: string) => void;
}) {
  if (secrets.length === 0) return null;
  return (
    <select
      aria-label="Insert a secret"
      className={`${fieldClass} h-8 w-fit text-[13px]`}
      value=""
      onChange={(e) => e.target.value && onPick(`{{secret:${e.target.value}}}`)}
    >
      <option value="">Insert a secret…</option>
      {secrets.map((secret) => (
        <option key={secret.name} value={secret.name}>
          {secret.name}
        </option>
      ))}
    </select>
  );
}

function insertAtCaret(field: HTMLTextAreaElement | null, text: string, insert: string): string {
  if (!field) return text + insert;
  const start = field.selectionStart ?? text.length;
  const end = field.selectionEnd ?? text.length;
  return text.slice(0, start) + insert + text.slice(end);
}

type LocalServerArgs = { command: string; args: string[]; env?: Record<string, string> };
type RemoteServerArgs = { server_url: string; headers?: Record<string, string>; auth?: "oauth"; oauth_app?: string };

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
  /** A sign-in waiting in the user's browser; the notice goes away once it connects. */
  signin?: { name: string; url: string };
}

const SIGN_IN_TEXT = "Finish signing in in your browser, a page was opened.";

function NoticeText({ notice, onApplyUpdate }: { notice: Notice; onApplyUpdate?: (pkg: string, version: string) => void }) {
  return (
    <p role="status" className={`text-sm ${notice.error ? "text-[var(--danger)]" : "text-[var(--muted)]"}`}>
      {notice.text}{" "}
      {notice.update && onApplyUpdate && (
        <button
          type="button"
          className="text-[var(--accent)] hover:underline"
          onClick={() => onApplyUpdate(notice.update!.pkg, notice.update!.version)}
        >
          Update
        </button>
      )}
      {notice.signin && (
        <a href={notice.signin.url} target="_blank" rel="noreferrer" className="text-[var(--accent)] hover:underline">
          Open the sign-in page
        </a>
      )}
    </p>
  );
}

/** An added connector: its tools and what each may do, and Disconnect. */
function ConnectorDetail({
  row,
  pending,
  notice,
  onBack,
  onDisconnect,
  onReconnect,
  onSignIn,
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
  onSignIn: () => void;
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
        <ConnectorIcon name={row.name} title={row.title} size="md" />
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

      {notice && !(notice.signin && info.signin) && <NoticeText notice={notice} onApplyUpdate={onApplyUpdate} />}

      {!info.connected ? (
        <div className="flex items-center gap-3 rounded-xl border border-[var(--border)] px-4 py-3 text-sm">
          <span className="flex-1 text-[var(--muted)]">
            {pending
              ? "Connecting…"
              : info.secret_error
                ? info.secret_error
                : info.signin
                  ? "Waiting for you to sign in in your browser."
                  : info.auth === "oauth"
                    ? (info.signin_error ?? "Not signed in. Its tools show up here once you sign in.")
                    : "Not connected. Its tools show up here once it connects."}
          </span>
          {info.signin && (
            <a href={info.signin.url} target="_blank" rel="noreferrer" className="text-[var(--accent)] hover:underline">
              Open the sign-in page
            </a>
          )}
          {info.signin ? (
            <button type="button" className={secondaryButtonClass} disabled={pending} onClick={onSignIn}>
              Start over
            </button>
          ) : (
            <>
              <button type="button" className={secondaryButtonClass} disabled={pending} onClick={onReconnect}>
                Retry
              </button>
              {info.auth === "oauth" && (
                <button type="button" className={primaryButtonClass} disabled={pending} onClick={onSignIn}>
                  Sign in
                </button>
              )}
            </>
          )}
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

const TOOLS_SHOWN = 18;

const infoLabel = "text-xs font-medium uppercase tracking-wide text-[var(--muted)]";

function ExternalLink({ href, children }: { href: string; children: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex w-fit items-center gap-1 text-[var(--accent)] underline-offset-2 hover:underline"
    >
      {children}
      <ExternalLinkIcon className="h-3.5 w-3.5" />
    </a>
  );
}

/** A connector from Discover that isn't added yet: what it is, what it can
 * do, who makes it, and Connect. */
function CatalogConnectorPage({
  entry,
  info,
  pending,
  notice,
  appSaved,
  redirectUri,
  onSetupChanged,
  onBack,
  onConnect,
  onSignIn,
  onManage,
  onDisconnect,
}: {
  entry: McpCatalogEntry;
  /** Set once the connector was added. */
  info: McpServerInfo | null;
  pending: boolean;
  notice: Notice | null;
  /** Whether the app this service needs (if any) has been registered and saved. */
  appSaved: boolean;
  redirectUri: string;
  onSetupChanged: () => void;
  onBack: () => void;
  onConnect: () => void;
  onSignIn: () => void;
  onManage: () => void;
  onDisconnect: () => void;
}) {
  const title = entry.title ?? entry.name;
  const pkg = findPinnedNpmPackage(entry.args ?? []);
  const tools = entry.tools ?? [];
  const [allTools, setAllTools] = useState(false);
  const shown = allTools ? tools : tools.slice(0, TOOLS_SHOWN);
  const needsSetup = entry.setup !== undefined && !appSaved;
  const showError = (text: string) => (entry.setup ? friendlySignInError(text, entry.setup, redirectUri) : text);
  const address = entry.server_url ?? (pkg ? `${pkg.name} ${pkg.version}` : `${entry.command ?? ""} ${(entry.args ?? []).join(" ")}`);
  return (
    <div className="flex flex-col gap-6">
      <button type="button" className="flex w-fit items-center gap-2 text-[15px] text-[var(--fg)] hover:opacity-70" onClick={onBack}>
        <ArrowLeftIcon className="h-4 w-4" /> Connectors
      </button>
      <div className="flex items-center gap-5 rounded-xl bg-[var(--card-bg)] px-6 py-6">
        <ConnectorIcon name={entry.name} title={title} size="lg" />
        <div className="min-w-0 flex-1">
          <div className="text-2xl font-semibold">{title}</div>
          <div className="mt-1 text-[15px] text-[var(--muted)]">{entry.description}</div>
        </div>
        {info?.connected && !pending ? (
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1.5 px-2 text-sm text-[var(--muted)]">
              <CheckIcon className="h-4 w-4" /> Connected
            </span>
            <button type="button" className={secondaryButtonClass} onClick={onManage}>
              Manage
            </button>
            <button type="button" className={secondaryButtonClass} onClick={onDisconnect}>
              Disconnect
            </button>
          </div>
        ) : info?.signin ? (
          <div className="flex items-center gap-2">
            <button type="button" className={secondaryButtonClass} onClick={onSignIn}>
              Start over
            </button>
            <button type="button" className={secondaryButtonClass} onClick={onDisconnect}>
              Cancel
            </button>
          </div>
        ) : info && !pending ? (
          <div className="flex items-center gap-2">
            <button type="button" className={primaryButtonClass} onClick={info.auth === "oauth" ? onSignIn : onConnect}>
              {info.auth === "oauth" ? "Sign in" : "Reconnect"}
            </button>
            <button type="button" className={secondaryButtonClass} onClick={onDisconnect}>
              Remove
            </button>
          </div>
        ) : needsSetup ? (
          <button
            type="button"
            className={primaryButtonClass}
            onClick={() => document.getElementById(`setup-${entry.name}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}
          >
            Set up
          </button>
        ) : (
          <button
            type="button"
            disabled={pending}
            className={`${primaryButtonClass} ${pending ? "running-wave-ring" : ""}`}
            onClick={onConnect}
          >
            {pending ? "Connecting…" : "Connect"}
          </button>
        )}
      </div>
      {info?.signin ? (
        <p role="status" className="text-sm text-[var(--muted)]">
          Waiting for you to sign in in your browser.{" "}
          <a href={info.signin.url} target="_blank" rel="noreferrer" className="text-[var(--accent)] hover:underline">
            Open the sign-in page
          </a>
        </p>
      ) : info && !info.connected && !pending ? (
        <p role="status" className="text-sm text-[var(--danger)]">
          {showError(info.secret_error ?? info.signin_error ?? "Not connected.")}
        </p>
      ) : (
        notice && <NoticeText notice={{ ...notice, text: notice.error ? showError(notice.text) : notice.text }} />
      )}
      {entry.setup && (
        <ConnectorSetup entry={entry} setup={entry.setup} saved={appSaved} redirectUri={redirectUri} onChanged={onSetupChanged} />
      )}
      {entry.about && <p className="max-w-[46rem] text-[15px] leading-relaxed">{entry.about}</p>}

      <section>
        <h3 className="flex items-center gap-2 text-xl font-semibold">
          Tools
          {tools.length > 0 && (
            <span className="rounded-md bg-[var(--card-bg)] px-1.5 text-xs font-normal text-[var(--muted)]">{tools.length}</span>
          )}
        </h3>
        {tools.length > 0 ? (
          <>
            <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {shown.map((tool) => (
                <span key={tool} className="truncate rounded-lg bg-[var(--card-bg)] px-3 py-1.5 text-[13px]" title={tool}>
                  {tool}
                </span>
              ))}
            </div>
            {tools.length > TOOLS_SHOWN && (
              <button
                type="button"
                className="mt-3 text-sm text-[var(--accent)] hover:underline"
                onClick={() => setAllTools((v) => !v)}
              >
                {allTools ? "Show fewer" : `Show all ${tools.length}`}
              </button>
            )}
            <p className="mt-3 text-xs text-[var(--muted)]">
              {entry.tools_from === "service"
                ? "As the service reported them when it was last connected."
                : "From the maker's documentation. Once connected, this page lists what the service offers now."}{" "}
              The connector's own page lets you choose which tools may run.
            </p>
          </>
        ) : (
          <p className="mt-2 text-sm text-[var(--muted)]">
            The maker doesn't publish a list. Once you connect, the service reports its tools and they show here.
          </p>
        )}
      </section>

      <div className="grid grid-cols-1 gap-x-12 gap-y-6 border-t border-[var(--border)] pt-6 text-sm sm:grid-cols-2">
        {entry.made_by && (
          <div className="flex flex-col gap-1">
            <div className={infoLabel}>Made by</div>
            {entry.homepage ? <ExternalLink href={entry.homepage}>{entry.made_by}</ExternalLink> : <div>{entry.made_by}</div>}
          </div>
        )}
        <div className="flex flex-col gap-1">
          <div className={infoLabel}>{entry.server_url ? "Connector URL" : pkg ? "Package" : "Command"}</div>
          <div className="break-all font-mono text-[13px]">{address}</div>
        </div>
        {entry.category && (
          <div className="flex flex-col gap-1">
            <div className={infoLabel}>Category</div>
            <span className="w-fit rounded-lg border border-[var(--border)] px-2.5 py-1 text-[13px]">{entry.category}</span>
          </div>
        )}
        <div className="flex flex-col gap-1">
          <div className={infoLabel}>Sign-in</div>
          <div>{entry.auth === "oauth" ? "Required. You sign in on the service's own page, in your browser." : "Not required"}</div>
        </div>
        {entry.links && entry.links.length > 0 && (
          <div className="flex flex-col gap-1">
            <div className={infoLabel}>More info</div>
            {entry.links.map((link) => (
              <ExternalLink key={link.url} href={link.url}>
                {link.label}
              </ExternalLink>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/** One connector in Discover's grid. */
function DiscoverCard({
  entry,
  added,
  needsSetup,
  pending,
  onOpen,
  onAdd,
}: {
  entry: McpCatalogEntry;
  added: boolean;
  needsSetup: boolean;
  pending: boolean;
  onOpen: () => void;
  onAdd: () => void;
}) {
  const title = entry.title ?? entry.name;
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Open ${title}`}
      className="relative flex cursor-pointer gap-4 rounded-xl border border-[var(--border)] bg-[var(--card-bg)] p-4 outline-none hover:border-[var(--muted)] focus-visible:border-[var(--focus)]"
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      <ConnectorIcon name={entry.name} title={title} size="md" />
      <div className="min-w-0 flex-1 pr-8">
        <div className="text-[15px] font-medium">{title}</div>
        <div className="mt-0.5 line-clamp-2 text-sm text-[var(--fg)]">{entry.description}</div>
        {entry.made_by && (
          <div className="mt-1.5 text-sm text-[var(--muted)]">
            by {entry.made_by}
            {needsSetup && <span className="ml-2 rounded-full bg-[var(--bg)] px-2 py-0.5 text-xs">Needs setup</span>}
          </div>
        )}
      </div>
      {added ? (
        <CheckIcon aria-label="Added" className="absolute right-4 top-4 h-4 w-4 text-[var(--muted)]" />
      ) : (
        <button
          type="button"
          aria-label={`Add ${title}`}
          disabled={pending}
          className={`absolute right-3 top-3 flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--bg)] hover:bg-[var(--card-bg)] ${
            pending ? "running-wave-ring" : ""
          }`}
          onClick={(e) => {
            e.stopPropagation();
            onAdd();
          }}
        >
          <PlusIcon className="h-4 w-4" />
        </button>
      )}
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
  const [headers, setHeaders] = useState("");
  const [secrets, setSecrets] = useState<SecretInfo[]>([]);
  const envRef = useRef<HTMLTextAreaElement>(null);
  const headersRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    getSecrets()
      .then((res) => setSecrets(res.secrets))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const trimmedName = name.trim();
  const nameTaken = existing.includes(trimmedName);
  const urlOk = /^https?:\/\/\S+$/i.test(serverUrl.trim());
  const ready = trimmedName !== "" && !nameTaken && (local ? command.trim() !== "" : urlOk);
  let serverHost: string | null = null;
  try {
    serverHost = urlOk ? new URL(serverUrl.trim()).hostname : null;
  } catch {
    serverHost = null;
  }
  // A header goes to this server only, so its secrets must be allowed for the
  // host; an env value goes to the local command, which has no host.
  const problems = local ? secretProblems(env, secrets, null) : secretProblems(headers, secrets, serverHost);

  const submit = () => {
    if (!ready) return;
    onAdd(
      trimmedName,
      local
        ? { command: command.trim(), args: args.split(/\s+/).filter(Boolean), env: parseEnvLines(env) }
        : { server_url: serverUrl.trim(), ...(headers.trim() ? { headers: parseHeaderLines(headers) } : {}) },
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
              ref={envRef}
              aria-label="Environment variables"
              className={`${fieldClass} h-auto min-h-20 w-full py-2 font-mono text-[13px]`}
              placeholder="Environment variables, one per line: KEY=value"
              value={env}
              onChange={(e) => setEnv(e.target.value)}
            />
            <SecretPicker secrets={secrets} onPick={(p) => setEnv(insertAtCaret(envRef.current, env, p))} />
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
            <textarea
              ref={headersRef}
              aria-label="Headers"
              className={`${fieldClass} mt-3 h-auto min-h-16 w-full py-2 font-mono text-[13px]`}
              placeholder="Headers, one per line: Authorization: Bearer {{secret:NAME}}"
              value={headers}
              onChange={(e) => setHeaders(e.target.value)}
            />
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
              <SecretPicker
                secrets={secrets}
                onPick={(p) => setHeaders(insertAtCaret(headersRef.current, headers, p))}
              />
              <span className="text-[13px] text-[var(--muted)]">
                A secret from Settings &gt; Secrets is sent to this server only, so it must be allowed for its host.
              </span>
            </div>
          </div>
        )}
        {problems.length > 0 && (
          <p role="alert" className="-mt-2 text-sm text-[var(--danger)]">
            {problems.join(" ")}
          </p>
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

function StatusCell({ row, pending }: { row: ConnectorRow; pending: boolean }) {
  const info = row.serverInfo;
  if (pending) {
    return <span className="running-wave-ring rounded-md px-2.5 py-1 text-sm">Connecting…</span>;
  }
  if (info?.connected) return <CheckIcon aria-label="Connected" className="h-4 w-4" />;
  if (info?.secret_error) {
    return (
      <span className="text-sm text-[var(--danger)]" title={info.secret_error}>
        Secret problem
      </span>
    );
  }
  if (info?.signin) return <span className="text-sm text-[var(--muted)]">Waiting for sign-in</span>;
  if (info?.auth === "oauth") return <span className="text-sm text-[var(--danger)]">Sign in</span>;
  return <span className="text-sm text-[var(--danger)]">Not connected</span>;
}

// Header and rows share it, so each column lines up with its heading.
const ROW_GRID = "grid grid-cols-[minmax(0,1fr)_10rem_8rem] items-center gap-4 px-3";

const EMPTY: { catalog: McpCatalogEntry[]; servers: McpServersResponse; apps: McpOAuthApps } = {
  catalog: [],
  servers: {},
  apps: { saved: [], redirect_uri: "" },
};

/** Settings > Connectors: the list, a connector's own page (its tools and
 * their permissions), and adding a custom one. */
export function ConnectorsTab({ active }: { active: boolean }) {
  const [, forceRender] = useState(0);
  const [tab, setTab] = useState<"yours" | "discover">("yours");
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
    data: { catalog, servers, apps },
    status,
    retry: refresh,
  } = useFetchOnActive(
    active,
    () =>
      Promise.all([getMcpCatalog(), getMcpServers(), getMcpOAuthApps()]).then(([cat, srv, saved]) => ({
        catalog: cat,
        servers: srv,
        apps: saved,
      })),
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
      else if (result.signin) setNotice({ text: SIGN_IN_TEXT, error: false, signin: { name, url: result.signin.url } });
      else if (result.connected) setNotice(null);
      else setNotice({ text: `Saved, but couldn't connect${result.error ? ` -- ${result.error}` : "."}`, error: true });
    });

  const needsSetup = (entry: McpCatalogEntry) => entry.setup !== undefined && !apps.saved.includes(entry.setup.group);

  const connectCatalog = (entry: McpCatalogEntry) => {
    setOpenName(entry.name);
    if (entry.needs_config) {
      setNotice({ text: `${entry.title ?? entry.name} needs its own token -- add it as a custom connector.`, error: false });
      return;
    }
    if (entry.setup && !apps.saved.includes(entry.setup.group)) return;
    if (entry.server_url) {
      void performAdd(entry.name, { server_url: entry.server_url, auth: entry.auth, oauth_app: entry.setup?.group });
    }
    else void performAdd(entry.name, { command: entry.command ?? "", args: entry.args ?? [] });
  };

  const signIn = (name: string) =>
    withPending(name, async () => {
      const result = await signInMcpServer(name);
      if (result.signin) setNotice({ text: SIGN_IN_TEXT, error: false, signin: { name, url: result.signin.url } });
      else if (result.connected) setNotice(null);
      else setNotice({ text: `Couldn't sign in${result.error ? ` -- ${result.error}` : "."}`, error: true });
    });

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

  useEffect(() => {
    if (notice?.signin && effectiveServers[notice.signin.name]?.connected) setNotice(null);
  }, [notice, effectiveServers]);

  const rows = buildRows(catalog, effectiveServers);
  const opened = openName ? rows.find((r) => r.name === openName) : undefined;
  const backToList = () => {
    setOpenName(null);
    setNotice(null);
  };

  // Discover is the store: a connector opened there shows its store page
  // whether or not it was added; Yours opens the page that manages it.
  if (opened?.catalogEntry && (tab === "discover" || !opened.serverInfo)) {
    return (
      <CatalogConnectorPage
        entry={opened.catalogEntry}
        info={opened.serverInfo}
        pending={pendingConnectorAdds.has(opened.name)}
        notice={notice}
        appSaved={opened.catalogEntry.setup ? apps.saved.includes(opened.catalogEntry.setup.group) : true}
        redirectUri={apps.redirect_uri}
        onSetupChanged={refresh}
        onBack={backToList}
        onConnect={() => connectCatalog(opened.catalogEntry!)}
        onSignIn={() => void signIn(opened.name)}
        onManage={() => setTab("yours")}
        onDisconnect={() => void disconnect(opened.name)}
      />
    );
  }
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
        onSignIn={() => void signIn(opened.name)}
        onCheckUpdate={(pkg) => void checkUpdate(pkg, pinned?.version)}
        onApplyUpdate={(pkg, version) => void applyUpdate(opened.name, pkg, version)}
        onPolicies={(policies) => void setPolicies(opened.name, policies)}
      />
    );
  }

  const q = search.trim().toLowerCase();
  const matches = (r: ConnectorRow) => `${r.title} ${r.name} ${r.catalogEntry?.description ?? ""}`.toLowerCase().includes(q);
  const yours = rows.filter((r) => r.serverInfo !== null && (!q || matches(r)));
  const discover = rows.filter((r) => r.catalogEntry !== null && (!q || matches(r)));
  const segment = (on: boolean) =>
    `rounded-md px-3.5 py-1 text-[15px] ${
      on ? "bg-[var(--bg)] text-[var(--fg)] shadow-sm ring-1 ring-[var(--border)]" : "text-[var(--muted)] hover:text-[var(--fg)]"
    }`;
  const openRow = (name: string) => {
    setNotice(null);
    setOpenName(name);
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="mr-1 text-[22px] font-semibold">Connectors</h2>
        <div className="flex rounded-lg bg-[var(--card-bg)] p-0.5">
          <button type="button" aria-pressed={tab === "yours"} className={segment(tab === "yours")} onClick={() => setTab("yours")}>
            Yours
          </button>
          <button type="button" aria-pressed={tab === "discover"} className={segment(tab === "discover")} onClick={() => setTab("discover")}>
            Discover
          </button>
        </div>
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
                  setTab("discover");
                }}
              >
                Browse Discover
              </button>
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

      {notice && <NoticeText notice={notice} />}

      <FetchRetry status={status} onRetry={refresh} />
      {tab === "yours" && status === "success" && yours.length === 0 && (
        <p className="py-6 text-center text-sm text-[var(--muted)]">
          {q ? "No connectors match." : "No connectors yet. Add one from "}
          {!q && (
            <button type="button" className="text-[var(--accent)] hover:underline" onClick={() => setTab("discover")}>
              Discover
            </button>
          )}
          {!q && "."}
        </p>
      )}
      {tab === "yours" && status === "success" && yours.length > 0 && (
        <div className="flex flex-col">
          <div className={`${ROW_GRID} border-b border-[var(--border)] pb-2 text-[13px] text-[var(--muted)]`}>
            <span>Connector</span>
            <span>Type</span>
            <span>Status</span>
          </div>
          {yours.map((row) => (
            <div key={row.name} className="border-b border-[var(--border)] py-1 last:border-b-0">
              <div
                role="button"
                tabIndex={0}
                aria-label={`Open ${row.title}`}
                className={`${ROW_GRID} cursor-pointer rounded-lg py-2 outline-none hover:bg-[var(--card-bg)] focus-visible:bg-[var(--card-bg)]`}
                onClick={() => openRow(row.name)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    openRow(row.name);
                  }
                }}
              >
                <div className="flex min-w-0 items-center gap-3">
                  <ConnectorIcon name={row.name} title={row.title} />
                  <span className="truncate text-[15px]">{row.title}</span>
                </div>
                <div className="flex items-center text-[15px]">
                  {(row.serverInfo?.server_url ?? row.catalogEntry?.server_url) !== undefined ? "Web" : "Local"}
                  {!row.catalogEntry && (
                    <span className="ml-2 rounded-md bg-[var(--card-bg)] px-1.5 py-0.5 text-xs text-[var(--muted)]">Custom</span>
                  )}
                </div>
                <div className="flex items-center">
                  <StatusCell row={row} pending={pendingConnectorAdds.has(row.name)} />
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === "discover" && status === "success" && (
        <div className="flex flex-col gap-4">
          <h3 className="flex items-center gap-2 text-[17px] font-medium">
            Connectors <span className="rounded-md bg-[var(--card-bg)] px-1.5 text-xs text-[var(--muted)]">{discover.length}</span>
          </h3>
          {discover.length === 0 ? (
            <p className="py-6 text-center text-sm text-[var(--muted)]">No connectors match "{search.trim()}".</p>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {discover.map((row) => (
                <DiscoverCard
                  key={row.name}
                  entry={row.catalogEntry!}
                  added={row.serverInfo !== null}
                  needsSetup={needsSetup(row.catalogEntry!)}
                  pending={pendingConnectorAdds.has(row.name)}
                  onOpen={() => openRow(row.name)}
                  onAdd={() => (needsSetup(row.catalogEntry!) ? openRow(row.name) : connectCatalog(row.catalogEntry!))}
                />
              ))}
            </div>
          )}
          <p className="text-xs text-[var(--muted)]">
            Hosted services you sign in to in your own browser: nothing to install, no app to register. Anything else can
            be added with Add &gt; Add custom connector.
          </p>
        </div>
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
