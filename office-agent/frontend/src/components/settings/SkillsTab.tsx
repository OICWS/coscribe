import { useRef, useState } from "react";
import { getSkills, uploadSkill } from "../../lib/rest";
import { useClickOutside } from "../../lib/useClickOutside";
import { useFetchOnActive } from "../../lib/useFetchOnActive";
import type { SkillInfo } from "../../types/settings";
import { ArrowLeftIcon, BookOpenIcon, ChevronDownIcon, PencilIcon, SearchIcon, UploadIcon } from "../icons";
import { ToggleSwitch } from "../ToggleSwitch";
import { FetchRetry } from "./FetchRetry";

interface SkillsTabProps {
  active: boolean;
  enabledSkills: string[];
  onToggle: (name: string, enabled: boolean) => void;
  /** "Create a skill" in the Add menu -- closes Settings and prefills the
   * composer with "/skill-creator" (see App.tsx's pendingComposerText),
   * same shape as externalImage/onExternalImageConsumed for an image. */
  onCreateSkill: () => void;
}

type SkillsView = "list" | "upload";
type SkillsSourceTab = "yours" | "discover";

/** One skill row -- icon, name, description, the existing per-thread
 * enable toggle. Top-level (not nested in SkillsTab) so it isn't
 * recreated every render. */
function SkillRow({ skill, enabled, onToggle }: { skill: SkillInfo; enabled: boolean; onToggle: () => void }) {
  return (
    <div className="flex items-center gap-3 border-b border-[var(--border)] py-3 last:border-b-0">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[var(--border)] text-[var(--muted)]">
        <BookOpenIcon className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">{skill.name}</div>
        <div className="truncate text-xs text-[var(--muted)]" title={skill.description}>
          {skill.source === "custom" ? "by you" : "built into coscribe"} &middot; {skill.description}
        </div>
      </div>
      <ToggleSwitch on={enabled} onClick={onToggle} />
    </div>
  );
}

/** Settings > Skills > Add > Upload skill -- the real half of
 * docs/ui-references/skills-add-uploadskills.png (no security-scan UI,
 * we don't scan). Top-level, not nested in SkillsTab. */
function UploadSkillView({ onBack, onUploaded }: { onBack: () => void; onUploaded: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const save = async () => {
    if (!file) return;
    setSaving(true);
    setError(null);
    const result = await uploadSkill(file);
    setSaving(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    onUploaded();
  };

  return (
    <div className="flex flex-col gap-4">
      <button
        type="button"
        className="flex w-fit items-center gap-1.5 text-sm text-[var(--muted)] hover:text-[var(--fg)]"
        onClick={onBack}
      >
        <ArrowLeftIcon className="h-3.5 w-3.5" /> Skills
      </button>
      <div>
        <h2 className="text-lg font-semibold">Upload skill</h2>
        <p className="mt-1 text-sm text-[var(--muted)]">Add a skill to your workspace.</p>
      </div>
      <div>
        <div className="mb-1.5 text-sm font-medium">Skill file</div>
        <div
          className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-10 text-center ${
            dragging ? "border-[var(--accent)] bg-[var(--card-bg)]" : "border-[var(--border)]"
          }`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const dropped = e.dataTransfer.files[0];
            if (dropped) setFile(dropped);
          }}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
        >
          <UploadIcon className="h-5 w-5 text-[var(--muted)]" />
          {file ? (
            <span className="text-sm font-medium">{file.name}</span>
          ) : (
            <span className="text-sm text-[var(--muted)]">Drag and drop a skill file here, or browse</span>
          )}
          <input
            ref={inputRef}
            type="file"
            accept=".md,.zip,.skill"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>
        <ul className="mt-2 list-disc pl-5 text-xs text-[var(--muted)]">
          <li>.md file must contain skill name and description formatted in YAML</li>
          <li>.zip or .skill file must include a SKILL.md file</li>
        </ul>
      </div>
      <div className="flex items-center gap-3">
        <button
          type="button"
          disabled={!file || saving}
          className="rounded-md bg-[var(--accent)] px-4 py-1.5 text-sm font-medium text-[var(--accent-fg)] disabled:opacity-40"
          onClick={save}
        >
          {saving ? "Saving..." : "Save"}
        </button>
        <button type="button" className="rounded-md border border-[var(--border)] px-4 py-1.5 text-sm" onClick={onBack}>
          Cancel
        </button>
        {error && <span className="text-sm text-red-500">{error}</span>}
        {!file && !error && <span className="text-sm text-[var(--muted)]">Choose a file to continue.</span>}
      </div>
    </div>
  );
}

/** Settings > Skills, restructured toward docs/ui-references/
 * skills-tab.png + skills-add-subchoice.png/skills-add-uploadskills.png:
 * "Your skills" (settings.skills_dir, user-authored/uploaded) vs
 * "Discover" (coscribe's own built-in pptx/excel/word/skill-creator) as
 * two tabs -- coscribe's real two-source split standing in for the
 * reference's "your installs" vs "marketplace" (there's no marketplace
 * here, "Discover" is literally "decide which of our built-ins are on").
 * The per-thread enable ToggleSwitch (coscribe's own mechanism, not in
 * the reference) applies in both tabs. Add offers exactly two of the
 * reference's three actions (skipping "Create with Claude", no coscribe
 * equivalent): Upload skill (real, writes into skills_dir via
 * POST /api/skills/upload) and Create a skill (prefills "/skill-creator"
 * in the composer, see onCreateSkill). No Filter/Sort/dates/kebab menu --
 * no metadata or actions behind any of them yet, see ROADMAP.md's Phase
 * 8am item 4 for the full scope discussion. */
export function SkillsTab({ active, enabledSkills, onToggle, onCreateSkill }: SkillsTabProps) {
  const { data: skills, status, retry } = useFetchOnActive(active, getSkills, []);
  const [view, setView] = useState<SkillsView>("list");
  const [tab, setTab] = useState<SkillsSourceTab>("yours");
  const [search, setSearch] = useState("");
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const addMenuRef = useRef<HTMLDivElement>(null);
  useClickOutside(addMenuRef, () => setAddMenuOpen(false), addMenuOpen);

  if (view === "upload") {
    return (
      <UploadSkillView
        onBack={() => setView("list")}
        onUploaded={() => {
          setView("list");
          setTab("yours");
          retry();
        }}
      />
    );
  }

  const enabledSet = new Set(enabledSkills);
  const activeList = skills.filter((s) => s.source === (tab === "yours" ? "custom" : "builtin"));
  const q = search.trim().toLowerCase();
  const filtered = q
    ? activeList.filter((s) => s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q))
    : activeList;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">Skills</h2>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-2 rounded-md border border-[var(--border)] px-2.5 py-1.5">
            <SearchIcon className="h-3.5 w-3.5 shrink-0 text-[var(--muted)]" />
            <input
              className="w-40 min-w-0 bg-transparent text-sm outline-none placeholder:text-[var(--muted)]"
              placeholder="Search skills"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="relative" ref={addMenuRef}>
            <button
              type="button"
              className="flex items-center gap-1 rounded-md bg-[var(--fg)] px-3 py-1.5 text-sm font-medium text-[var(--bg)]"
              onClick={() => setAddMenuOpen((v) => !v)}
            >
              Add <ChevronDownIcon className="h-3.5 w-3.5" />
            </button>
            {addMenuOpen && (
              <div className="absolute right-0 top-full z-10 mt-1 min-w-44 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] py-1 shadow-[var(--shadow)]">
                <button
                  type="button"
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]"
                  onClick={() => {
                    setAddMenuOpen(false);
                    setView("upload");
                  }}
                >
                  <UploadIcon className="h-3.5 w-3.5" /> Upload skill
                </button>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]"
                  onClick={() => {
                    setAddMenuOpen(false);
                    onCreateSkill();
                  }}
                >
                  <PencilIcon className="h-3.5 w-3.5" /> Create a skill
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="flex gap-4 border-b border-[var(--border)] text-sm">
        {(["yours", "discover"] as const).map((t) => (
          <button
            key={t}
            type="button"
            className={`-mb-px border-b-2 py-1.5 ${
              tab === t ? "border-[var(--fg)] font-medium text-[var(--fg)]" : "border-transparent text-[var(--muted)] hover:text-[var(--fg)]"
            }`}
            onClick={() => setTab(t)}
          >
            {t === "yours" ? "Your skills" : "Discover"}
          </button>
        ))}
      </div>

      <p className="text-xs text-[var(--muted)]">
        {tab === "yours"
          ? "Skills you've written or uploaded yourself, from your local skills directory."
          : "coscribe's own built-in skills -- deeper design guidance for pptx/excel/word and a skill-creator meta-skill. Turn any of them off if you don't want that guidance applied."}
      </p>

      <FetchRetry status={status} onRetry={retry} />

      {status === "success" && (
        <div>
          <div className="mb-1 flex items-center gap-1.5 text-sm font-medium">
            {tab === "yours" ? "Created by you" : "Built into coscribe"}
            <span className="text-[var(--muted)]">&middot; {filtered.length}</span>
          </div>
          {filtered.length === 0 && (
            <div className="py-2 text-sm text-[var(--muted)]">
              {activeList.length === 0
                ? tab === "yours"
                  ? "No skills yet -- use Add above to create or upload one."
                  : "Nothing here."
                : "No matches."}
            </div>
          )}
          {filtered.map((skill) => (
            <SkillRow
              key={skill.name}
              skill={skill}
              enabled={enabledSet.has(skill.name)}
              onToggle={() => onToggle(skill.name, !enabledSet.has(skill.name))}
            />
          ))}
        </div>
      )}
    </div>
  );
}
