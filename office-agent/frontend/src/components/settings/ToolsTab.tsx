import { getTools } from "../../lib/rest";
import type { ToolInfo } from "../../types/settings";
import { FetchRetry } from "./FetchRetry";
import { useFetchOnActive } from "../../lib/useFetchOnActive";

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
    <div className="flex flex-col gap-4">
      <p className="text-xs text-[var(--muted)]">
        Built-in tools the Coordinator can call directly. See Connectors for MCP server tools and Providers for
        LLM providers.
      </p>
      <FetchRetry status={status} onRetry={retry} />
      {[...byCategory.entries()].map(([category, list]) => (
        <div key={category}>
          <h4 className="mb-1 text-sm font-medium">{category}</h4>
          <div className="flex flex-col gap-1">
            {list.map((tool) => (
              <div key={tool.name} className="rounded-lg border border-[var(--border)] px-3 py-2 text-sm">
                <div className="flex items-center gap-2">
                  <span className="font-mono">{tool.name}</span>
                  {tool.requires_approval && (
                    <span
                      className="rounded-full border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--muted)]"
                      title={`risk category: ${tool.risk_category}`}
                    >
                      requires approval &middot; {tool.risk_category}
                    </span>
                  )}
                </div>
                {tool.description && <div className="text-xs text-[var(--muted)]">{tool.description}</div>}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
