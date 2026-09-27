import { getTools } from "../../lib/rest";
import type { ToolInfo } from "../../types/settings";
import { FetchRetry } from "./FetchRetry";
import { useFetchOnActive } from "../../lib/useFetchOnActive";
import { SettingRows, SettingsSection } from "./SettingRow";

interface ToolsTabProps {
  active: boolean;
}

export function ToolsTab({ active }: ToolsTabProps) {
  const { data: tools, status, retry } = useFetchOnActive(active, () => getTools().then((res) => res.tools), []);

  const byCategory = new Map<string, ToolInfo[]>();
  for (const tool of tools) {
    const category = tool.category || "other";
    const list = byCategory.get(category) ?? [];
    list.push(tool);
    byCategory.set(category, list);
  }

  return (
    <div className="flex flex-col gap-10">
      <FetchRetry status={status} onRetry={retry} />
      {[...byCategory.entries()]
        .sort(([a], [b]) => Number(a.startsWith("mcp:")) - Number(b.startsWith("mcp:")))
        .map(([category, list]) => (
        <SettingsSection key={category} title={categoryTitle(category)}>
          <SettingRows>
            {list.map((tool) => (
              <div key={tool.name} className="flex items-start justify-between gap-6 py-3">
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
          </SettingRows>
        </SettingsSection>
      ))}
    </div>
  );
}

/** "files" -> "Files", "mcp:office365" -> "Connector: office365". */
function categoryTitle(category: string): string {
  if (category.startsWith("mcp:")) return `Connector: ${category.slice(4)}`;
  return category.charAt(0).toUpperCase() + category.slice(1).replace(/[_-]/g, " ");
}
