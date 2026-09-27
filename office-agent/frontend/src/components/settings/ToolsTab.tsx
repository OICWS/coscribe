import { useState } from "react";
import { ChevronDownIcon } from "../icons";
import { getTools } from "../../lib/rest";
import type { ToolInfo } from "../../types/settings";
import { FetchRetry } from "./FetchRetry";
import { useFetchOnActive } from "../../lib/useFetchOnActive";
import { SettingsSection } from "./SettingRow";

interface ToolsTabProps {
  active: boolean;
}

export function ToolsTab({ active }: ToolsTabProps) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const { data: tools, status, retry } = useFetchOnActive(active, () => getTools().then((res) => res.tools), []);

  const byCategory = new Map<string, ToolInfo[]>();
  for (const tool of tools) {
    const category = tool.category || "other";
    const list = byCategory.get(category) ?? [];
    list.push(tool);
    byCategory.set(category, list);
  }

  const toggle = (category: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });

  return (
    <SettingsSection
      title="Tools"
      description="What coscribe can do on its own. Tools marked Needs approval ask first, depending on the conversation's mode."
    >
      <FetchRetry status={status} onRetry={retry} />
      <div className="mt-3 flex flex-col divide-y divide-[var(--border)] border-y border-[var(--border)]">
        {[...byCategory.entries()]
          .sort(([a], [b]) => Number(a.startsWith("mcp:")) - Number(b.startsWith("mcp:")))
          .map(([category, list]) => {
            const expanded = open.has(category);
            return (
              <div key={category}>
                <button
                  type="button"
                  aria-expanded={expanded}
                  className="flex w-full items-center gap-3 py-3.5 text-left hover:text-[var(--fg)]"
                  onClick={() => toggle(category)}
                >
                  <span className="flex-1 text-[15px]">{categoryTitle(category)}</span>
                  <span className="text-[13px] text-[var(--muted)]">
                    {list.length} {list.length === 1 ? "tool" : "tools"}
                  </span>
                  <ChevronDownIcon className={`h-4 w-4 text-[var(--muted)] transition-transform ${expanded ? "" : "-rotate-90"}`} />
                </button>
                {expanded && (
                  <div className="mb-3 flex flex-col divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]">
                    {list.map((tool) => (
                      <div key={tool.name} className="flex items-start justify-between gap-6 px-4 py-3">
                        <div className="min-w-0">
                          <div className="font-mono text-[13px] text-[var(--fg)]">{tool.name}</div>
                          {tool.description && (
                            <div className="mt-0.5 line-clamp-2 text-[13px] leading-snug text-[var(--muted)]">{tool.description}</div>
                          )}
                        </div>
                        {tool.requires_approval && (
                          <span
                            className="mt-0.5 shrink-0 rounded-full border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--muted)]"
                            title={`Risk: ${tool.risk_category}`}
                          >
                            Needs approval
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
      </div>
    </SettingsSection>
  );
}

/** "files" -> "Files", "mcp:office365" -> "Connector: office365". */
function categoryTitle(category: string): string {
  if (category.startsWith("mcp:")) return `Connector: ${category.slice(4)}`;
  return category.charAt(0).toUpperCase() + category.slice(1).replace(/[_-]/g, " ");
}
