import { type ReactNode, useEffect, useState } from "react";
import { getWorkflowPermissions, type PermissionsResult } from "../../lib/rest";
import type { Workflow } from "../../types/workflow";
import { AlertCircleIcon, FolderIcon, GlobeIcon, TerminalIcon } from "../icons";

interface Props {
  workflow: Workflow;
  /** A saved task: what its runs were allowed since counts too. */
  triggerId?: string;
  /** Mark what this version adds to the task's saved one. */
  againstSaved?: boolean;
  /** Changes when something was allowed since, so the list is read again. */
  version?: string;
  className?: string;
}

function New() {
  return (
    <span className="ml-1.5 rounded-full bg-[color-mix(in_srgb,var(--warning)_18%,transparent)] px-1.5 py-0.5 text-[11px] font-medium text-[var(--fg)]">
      New
    </span>
  );
}

function Row({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 px-3.5 py-2.5">
      <span className="mt-0.5 h-4 w-4 shrink-0 text-[var(--muted)]">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-medium">{label}</div>
        <div className="mt-0.5 text-[13px] text-[var(--muted)]">{children}</div>
      </div>
    </div>
  );
}

function List({ items, fresh }: { items: string[]; fresh: string[] }) {
  return (
    <>
      {items.map((item, index) => (
        <span key={item} className="break-all">
          {index > 0 && ", "}
          <span className="text-[var(--fg)]">{item}</span>
          {fresh.includes(item) && <New />}
        </span>
      ))}
    </>
  );
}

/** What a workflow will be able to do once saved -- saving is the consent,
 * and a run that needs more stops and asks. Plain about what scripts are:
 * checked for where they write, not contained. */
export function WorkflowPermissions({
  workflow,
  triggerId,
  againstSaved = false,
  version = "",
  className = "mt-5",
}: Props) {
  const [result, setResult] = useState<PermissionsResult | null>(null);
  const key = JSON.stringify(workflow);

  useEffect(() => {
    let current = true;
    getWorkflowPermissions(workflow, triggerId, againstSaved).then((next) => {
      if (current && !("error" in next)) setResult(next);
    });
    return () => {
      current = false;
    };
    // The workflow's content, not its identity, decides when to ask again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, triggerId, againstSaved, version]);

  if (!result) return null;
  const { permissions: p, added } = result;
  const fresh = {
    sites: added?.sites ?? [],
    folders: added?.folders ?? [],
    scripts: added?.scripts ?? [],
    notable: added?.notable.map((n) => n.tool) ?? [],
  };
  return (
    <section
      aria-label="Permissions"
      data-testid="workflow-permissions"
      className={`overflow-hidden rounded-xl border border-[var(--border)] ${className}`}
    >
      <div className="border-b border-[var(--border)] bg-[var(--card-bg)] px-3.5 py-2.5">
        <div className="text-sm font-medium">What this workflow can do</div>
        <div className="text-[13px] text-[var(--muted)]">
          Saving allows exactly this. Anything else makes a run stop and ask you first.
        </div>
      </div>
      <div className="divide-y divide-[var(--border)]">
        <Row icon={<GlobeIcon className="h-4 w-4" />} label="Websites">
          {p.sites.length > 0 ? <List items={p.sites} fresh={fresh.sites} /> : "None named in its steps."}
          {p.sites_at_run_time.length > 0 && (
            <div>Decided at run time in: {p.sites_at_run_time.join(", ")}. Those stop and ask.</div>
          )}
        </Row>
        <Row icon={<FolderIcon className="h-4 w-4" />} label="Folders it can change">
          Its workspace
          {p.folders.length > 0 && (
            <>
              , and <List items={p.folders} fresh={fresh.folders} />
            </>
          )}
        </Row>
        {p.scripts.length > 0 && (
          <Row icon={<TerminalIcon className="h-4 w-4" />} label="Runs scripts">
            <List items={p.scripts} fresh={fresh.scripts} />
            <div>
              Scripts aren’t sandboxed: they can read any file this app can and use the network. They’re checked so
              they only write in the folders above, which guards against mistakes, not against harm.
            </div>
          </Row>
        )}
        {p.notable.length > 0 && (
          <Row icon={<AlertCircleIcon className="h-4 w-4" />} label="Acts outside this computer">
            {p.notable.map((n, index) => (
              <span key={`${n.tool}-${index}`}>
                {index > 0 && ", "}
                <span className="text-[var(--fg)]">{n.title}</span>
                {fresh.notable.includes(n.tool) && <New />}
              </span>
            ))}
          </Row>
        )}
      </div>
    </section>
  );
}
