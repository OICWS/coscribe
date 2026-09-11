import { getSkills } from "../../lib/rest";
import { ToggleSwitch } from "../ToggleSwitch";
import { FetchRetry } from "./FetchRetry";
import { useFetchOnActive } from "../../lib/useFetchOnActive";

interface SkillsTabProps {
  active: boolean;
  enabledSkills: string[];
  onToggle: (name: string, enabled: boolean) => void;
}

/** Multi-select, live-toggleable -- a skill can be turned on/off any
 * number of times per thread, and more than one can be active at once.
 * Modeled on
 * ToolsTab's fetch-on-active pattern for the list itself, but each row is
 * a checkbox bound to the live per-thread enabled set rather than a static
 * badge -- toggling sends the full next set over the WebSocket immediately
 * (see App.tsx's onToggleSkill), no separate Save step. */
export function SkillsTab({ active, enabledSkills, onToggle }: SkillsTabProps) {
  const { data: skills, status, retry } = useFetchOnActive(active, getSkills, []);

  const enabledSet = new Set(enabledSkills);

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-[var(--muted)]">
        Turn on a skill to give the Coordinator deeper design guidance for that file type --
        color palettes, layout rules, and format-specific conventions on top of what it already
        knows. Off by default; more than one can be on at once, and toggling takes effect
        immediately for this conversation.
      </p>
      <FetchRetry status={status} onRetry={retry} />
      <div className="flex flex-col">
        {skills.map((skill) => (
          <div
            key={skill.name}
            className="flex items-center justify-between gap-4 border-b border-[var(--border)] py-3 last:border-b-0"
          >
            <div className="min-w-0">
              <div className="text-sm font-medium">{skill.name}</div>
              {skill.description && (
                <div className="mt-0.5 text-xs text-[var(--muted)]">{skill.description}</div>
              )}
            </div>
            <ToggleSwitch
              on={enabledSet.has(skill.name)}
              onClick={() => onToggle(skill.name, !enabledSet.has(skill.name))}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
