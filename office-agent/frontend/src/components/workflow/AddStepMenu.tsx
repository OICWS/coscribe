import { useEffect, useMemo, useRef, useState } from "react";
import { controlClass } from "../../lib/formStyles";
import { toolLabel } from "../../lib/toolLabels";
import { useClickOutside } from "../../lib/useClickOutside";
import { UNAVAILABLE_TOOLS } from "../../lib/workflowEdit";
import type { ToolInfo } from "../../types/settings";
import type { StepKind } from "../../types/workflow";
import { ArrowLeftIcon, ChevronRightIcon } from "../icons";
import { KindTile } from "./parts";

const KINDS: { kind: StepKind; label: string; description: string }[] = [
  { kind: "tool", label: "Tool", description: "Use one of coscribe's tools -- no model involved" },
  { kind: "script", label: "Script", description: "Run a fixed Python script you've reviewed" },
  { kind: "llm", label: "Model", description: "One model call that must return set fields" },
  { kind: "check", label: "Check", description: "Stop the run unless values hold" },
  { kind: "approval", label: "Approval", description: "Pause until you approve" },
];

const STRUCTURE: { kind: StepKind; label: string; description: string }[] = [
  { kind: "branch", label: "If / otherwise", description: "Run different steps depending on a value" },
  { kind: "loop", label: "For each", description: "Repeat steps for every item in a list" },
];

function categoryLabel(category: string): string {
  if (!category) return "Other";
  const text = category.replace(/_/g, " ").replace(/^mcp:/, "Connector: ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

interface AddStepMenuProps {
  tools: ToolInfo[];
  onPick: (kind: StepKind, tool?: ToolInfo) => void;
  onClose: () => void;
  /** Opens upward when the trigger sits near the bottom of the list. */
  placement?: "below" | "above";
}

export function AddStepMenu({ tools, onPick, onClose, placement = "below" }: AddStepMenuProps) {
  const [view, setView] = useState<"kinds" | "tools">("kinds");
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  useClickOutside(ref, onClose, true);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (view === "tools") searchRef.current?.focus();
  }, [view]);

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matching = tools.filter(
      (tool) =>
        !UNAVAILABLE_TOOLS.has(tool.name) &&
        (!needle ||
          tool.name.includes(needle.replace(/\s+/g, "_")) ||
          toolLabel(tool.name).toLowerCase().includes(needle) ||
          tool.description.toLowerCase().includes(needle)),
    );
    const byCategory = new Map<string, ToolInfo[]>();
    for (const tool of matching) {
      const key = categoryLabel(tool.category);
      byCategory.set(key, [...(byCategory.get(key) ?? []), tool]);
    }
    return [...byCategory.entries()];
  }, [tools, query]);

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label="Add a step"
      className={`absolute left-0 z-30 w-[340px] overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--panel-bg)] shadow-[var(--shadow)] ${
        placement === "above" ? "bottom-full mb-2" : "top-full mt-2"
      }`}
    >
      {view === "kinds" ? (
        <ul className="flex flex-col p-1.5">
          {[...KINDS, ...STRUCTURE].map(({ kind, label, description }) => (
            <li
              key={kind}
              className={kind === STRUCTURE[0].kind ? "mt-1.5 border-t border-[var(--border)] pt-1.5" : undefined}
            >
              <button
                type="button"
                className="flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left hover:bg-[var(--card-bg)] focus-visible:bg-[var(--card-bg)] focus-visible:outline-none"
                onClick={() => (kind === "tool" ? setView("tools") : onPick(kind))}
              >
                <KindTile kind={kind} />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">{label}</span>
                  <span className="block text-xs text-[var(--muted)]">{description}</span>
                </span>
                {kind === "tool" && <ChevronRightIcon className="h-4 w-4 shrink-0 text-[var(--muted)]" />}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="flex max-h-[420px] flex-col">
          <div className="flex items-center gap-2 border-b border-[var(--border)] p-2">
            <button
              type="button"
              aria-label="Back to step kinds"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
              onClick={() => setView("kinds")}
            >
              <ArrowLeftIcon className="h-4 w-4" />
            </button>
            <input
              ref={searchRef}
              aria-label="Search tools"
              className={`${controlClass} min-w-0 flex-1 py-1`}
              placeholder="Search tools"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="overflow-y-auto p-1.5">
            {groups.length === 0 && <p className="px-2.5 py-3 text-sm text-[var(--muted)]">No tool matches.</p>}
            {groups.map(([category, items]) => (
              <section key={category} className="mb-1">
                <h3 className="px-2.5 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--muted)]">
                  {category}
                </h3>
                <ul>
                  {items.map((tool) => (
                    <li key={tool.name}>
                      <button
                        type="button"
                        className="w-full rounded-lg px-2.5 py-1.5 text-left hover:bg-[var(--card-bg)] focus-visible:bg-[var(--card-bg)] focus-visible:outline-none"
                        onClick={() => onPick("tool", tool)}
                      >
                        <span className="block text-sm">{toolLabel(tool.name)}</span>
                        {tool.description && (
                          <span className="block truncate text-xs text-[var(--muted)]">{tool.description}</span>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
