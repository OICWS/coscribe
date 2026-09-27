import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  addCatalogSkill,
  getSkillCatalog,
  getSkillFileContent,
  getSkillFiles,
  getSkills,
  getTools,
  removeSkill,
  setSkillEnabled,
  uploadSkill,
} from "../../lib/rest";
import { useClickOutside } from "../../lib/useClickOutside";
import { useFetchOnActive } from "../../lib/useFetchOnActive";
import type { CatalogSkill, SkillFileContentResult, SkillInfo } from "../../types/settings";
import { ConfirmDialog } from "../ConfirmDialog";
import {
  ArrowLeftIcon,
  CheckIcon,
  ChevronDownIcon,
  CodeIcon,
  EyeIcon,
  FolderIcon,
  MoreIcon,
  PencilIcon,
  PlusIcon,
  SearchIcon,
  SkillIcon,
  UploadIcon,
} from "../icons";
import { ToggleSwitch } from "../ToggleSwitch";
import { FetchRetry } from "./FetchRetry";

const Markdown = lazy(() => import("../Markdown").then((m) => ({ default: m.Markdown })));

interface SkillsTabProps {
  active: boolean;
  /** "Create a skill" in the Add menu -- closes Settings and prefills the
   * composer with "/skill-creator". */
  onCreateSkill: () => void;
}

type View = { kind: "list" } | { kind: "upload" } | { kind: "detail"; name: string };
type Tab = "yours" | "discover";

const SOURCE_BYLINE: Record<SkillInfo["source"], string> = {
  custom: "by you",
  anthropic: "from Anthropic",
  builtin: "built into coscribe",
};

const SECTIONS: { source: SkillInfo["source"]; title: string }[] = [
  { source: "custom", title: "Created by you" },
  { source: "anthropic", title: "From Anthropic" },
  { source: "builtin", title: "Built into coscribe" },
];

// Plain text worth showing in the file viewer; anything else (HTML, fonts,
// images) is listed but not opened.
const PREVIEWABLE = new Set(["md", "txt", "py", "json", "yaml", "yml", "toml", "csv", "js", "ts", "sh", "ini", "cfg"]);

function extension(path: string): string {
  const name = path.split("/").pop() ?? "";
  return name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
}

function shortDate(iso: string): string {
  const date = new Date(iso);
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}

function matches(query: string, ...texts: string[]): boolean {
  const q = query.trim().toLowerCase();
  return !q || texts.some((t) => t.toLowerCase().includes(q));
}

function SkillBadge({ large }: { large?: boolean }) {
  return (
    <div
      className={`flex shrink-0 items-center justify-center rounded-xl border border-[var(--border)] bg-[var(--card-bg)] text-[var(--muted)] ${
        large ? "h-14 w-14" : "h-11 w-11"
      }`}
    >
      <SkillIcon className={large ? "h-6 w-6" : "h-5 w-5"} />
    </div>
  );
}

/** "Off"/"On" and, unless it ships with coscribe, Remove. */
function SkillMenu({
  skill,
  onToggle,
  onRemove,
}: {
  skill: SkillInfo;
  onToggle: () => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(ref, () => setOpen(false), open);
  const item = "flex w-full px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]";
  return (
    <div className="relative" ref={ref} onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        aria-label={`Options for ${skill.name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex h-8 w-8 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
        onClick={() => setOpen((v) => !v)}
      >
        <MoreIcon className="h-4 w-4" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-20 mt-1 min-w-36 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] py-1 shadow-[var(--shadow)]"
        >
          <button
            type="button"
            role="menuitem"
            className={item}
            onClick={() => {
              setOpen(false);
              onToggle();
            }}
          >
            {skill.enabled ? "Turn off" : "Turn on"}
          </button>
          {skill.source !== "builtin" && (
            <button
              type="button"
              role="menuitem"
              className={`${item} text-[var(--danger)]`}
              onClick={() => {
                setOpen(false);
                onRemove();
              }}
            >
              Remove
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function SkillRow({
  skill,
  onOpen,
  onToggle,
  onRemove,
}: {
  skill: SkillInfo;
  onOpen: () => void;
  onToggle: () => void;
  onRemove: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Open ${skill.name}`}
      className="flex w-full cursor-pointer items-center gap-4 border-b border-[var(--border)] py-3.5 text-left outline-none last:border-b-0 focus-visible:bg-[var(--card-bg)]"
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      <SkillBadge />
      <div className={`min-w-0 flex-1 ${skill.enabled ? "" : "opacity-55"}`}>
        <div className="flex items-center gap-2">
          <span className="truncate text-[15px]">{skill.name}</span>
          {!skill.enabled && (
            <span className="shrink-0 rounded bg-[var(--card-bg)] px-1.5 py-0.5 text-[11px] text-[var(--muted)]">Off</span>
          )}
        </div>
        <div className="truncate text-sm text-[var(--muted)]" title={skill.description}>
          {SOURCE_BYLINE[skill.source]} &middot; {skill.description}
        </div>
      </div>
      <span className="shrink-0 text-sm tabular-nums text-[var(--muted)]">{shortDate(skill.updated)}</span>
      <SkillMenu skill={skill} onToggle={onToggle} onRemove={onRemove} />
    </div>
  );
}

function CountBadge({ count }: { count: number }) {
  return (
    <span className="rounded-full bg-[var(--card-bg)] px-2 py-0.5 text-xs tabular-nums text-[var(--muted)]">{count}</span>
  );
}

interface FileTreeNode {
  dirs: Map<string, FileTreeNode>;
  files: string[];
}

function buildFileTree(paths: string[]): FileTreeNode {
  const root: FileTreeNode = { dirs: new Map(), files: [] };
  for (const path of paths) {
    const parts = path.split("/");
    let node = root;
    for (const part of parts.slice(0, -1)) {
      let child = node.dirs.get(part);
      if (!child) {
        child = { dirs: new Map(), files: [] };
        node.dirs.set(part, child);
      }
      node = child;
    }
    node.files.push(parts[parts.length - 1]);
  }
  return root;
}

function FileTreeView({
  node,
  prefix,
  depth,
  selectedPath,
  collapsed,
  onToggleDir,
  onSelectFile,
}: {
  node: FileTreeNode;
  prefix: string;
  depth: number;
  selectedPath: string | null;
  collapsed: Set<string>;
  onToggleDir: (path: string) => void;
  onSelectFile: (path: string) => void;
}) {
  const indent = { paddingLeft: 10 + depth * 18 };
  // SKILL.md first, as the entry point; then folders, then other files.
  const entry = node.files.filter((f) => f === "SKILL.md");
  const rest = node.files.filter((f) => f !== "SKILL.md").sort((a, b) => a.localeCompare(b));
  const fileButton = (fileName: string) => {
    const filePath = prefix ? `${prefix}/${fileName}` : fileName;
    return (
      <FileButton key={filePath} path={filePath} name={fileName} indent={indent} selected={filePath === selectedPath} onSelect={onSelectFile} />
    );
  };
  return (
    <>
      {entry.map(fileButton)}
      {[...node.dirs.keys()].sort().map((dirName) => {
        const dirPath = prefix ? `${prefix}/${dirName}` : dirName;
        const open = !collapsed.has(dirPath);
        return (
          <div key={dirPath}>
            <button
              type="button"
              aria-expanded={open}
              className="flex w-full items-center gap-2 rounded-md py-1.5 pr-2 text-left text-[15px] text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
              style={indent}
              onClick={() => onToggleDir(dirPath)}
            >
              <FolderIcon className="h-4 w-4 shrink-0" />
              <span className="min-w-0 flex-1 truncate">{dirName}</span>
              <ChevronDownIcon className={`h-3.5 w-3.5 shrink-0 transition-transform ${open ? "" : "-rotate-90"}`} />
            </button>
            {open && (
              <FileTreeView
                node={node.dirs.get(dirName)!}
                prefix={dirPath}
                depth={depth + 1}
                selectedPath={selectedPath}
                collapsed={collapsed}
                onToggleDir={onToggleDir}
                onSelectFile={onSelectFile}
              />
            )}
          </div>
        );
      })}
      {rest.map(fileButton)}
    </>
  );
}

function FileButton({
  path,
  name,
  indent,
  selected,
  onSelect,
}: {
  path: string;
  name: string;
  indent: { paddingLeft: number };
  selected: boolean;
  onSelect: (path: string) => void;
}) {
  return (
    <button
      type="button"
      title={path}
      className={`block w-full truncate rounded-md py-1.5 pr-2 text-left text-[15px] ${
        selected ? "bg-[var(--card-bg)] font-medium text-[var(--fg)]" : "text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
      }`}
      style={{ paddingLeft: indent.paddingLeft + (indent.paddingLeft > 10 ? 6 : 0) }}
      onClick={() => onSelect(path)}
    >
      {name}
    </button>
  );
}

/** SKILL.md's YAML header is metadata, shown in the header, not the page. */
function withoutFrontmatter(text: string): string {
  return text.replace(/^---\n[\s\S]*?\n---\n?/, "");
}

function CodeView({ text }: { text: string }) {
  const lines = text.replace(/\n$/, "").split("\n");
  return (
    <div className="font-mono text-[13px] leading-6">
      {lines.map((line, index) => (
        <div key={index} className="flex">
          <span className="w-10 shrink-0 select-none pr-4 text-right tabular-nums text-[var(--muted)]">{index + 1}</span>
          <span className="min-w-0 flex-1 whitespace-pre-wrap break-words border-l border-[var(--border)] pl-4">
            {line || " "}
          </span>
        </div>
      ))}
    </div>
  );
}

function FileViewer({ skillName, path }: { skillName: string; path: string }) {
  const [content, setContent] = useState<SkillFileContentResult | null>(null);
  const [mode, setMode] = useState<"preview" | "code">("preview");
  const previewable = PREVIEWABLE.has(extension(path));
  const markdown = extension(path) === "md";

  useEffect(() => {
    let cancelled = false;
    setContent(null);
    if (previewable) {
      getSkillFileContent(skillName, path)
        .catch(() => ({ error: "Couldn't load this file." }))
        .then((result) => !cancelled && setContent(result));
    }
    return () => {
      cancelled = true;
    };
  }, [skillName, path, previewable]);

  const segment = (active: boolean) =>
    `flex h-7 w-8 items-center justify-center rounded-md ${
      active ? "bg-[var(--bg)] text-[var(--fg)] shadow-sm ring-1 ring-[var(--border)]" : "text-[var(--muted)] hover:text-[var(--fg)]"
    }`;
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex h-12 shrink-0 items-center justify-between gap-3 px-5">
        <span className="truncate font-mono text-sm text-[var(--muted)]">/{path}</span>
        {markdown && (
          <div className="flex shrink-0 rounded-lg bg-[var(--card-bg)] p-0.5">
            <button type="button" title="Preview" aria-pressed={mode === "preview"} className={segment(mode === "preview")} onClick={() => setMode("preview")}>
              <EyeIcon className="h-4 w-4" />
            </button>
            <button type="button" title="Source" aria-pressed={mode === "code"} className={segment(mode === "code")} onClick={() => setMode("code")}>
              <CodeIcon className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
        {!previewable && <p className="pt-10 text-center text-sm text-[var(--muted)]">No preview for this file type.</p>}
        {previewable && !content && <p className="text-sm text-[var(--muted)]">Loading...</p>}
        {content && "error" in content && <p className="text-sm text-[var(--muted)]">{content.error}</p>}
        {content && "content" in content &&
          (markdown && mode === "preview" ? (
            <Suspense fallback={<p className="whitespace-pre-wrap text-sm">{content.content}</p>}>
              <Markdown text={withoutFrontmatter(content.content)} />
            </Suspense>
          ) : (
            <CodeView text={content.content} />
          ))}
      </div>
    </div>
  );
}

function SkillDetailView({
  skill,
  onBack,
  onToggle,
  onRemove,
}: {
  skill: SkillInfo;
  onBack: () => void;
  onToggle: () => void;
  onRemove: () => void;
}) {
  const [tab, setTab] = useState<"overview" | "contents">("contents");
  const [files, setFiles] = useState<string[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [mentionedTools, setMentionedTools] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    getSkillFiles(skill.name)
      .then((result) => {
        if (cancelled) return;
        setFiles(result.files);
        setSelected(result.files.includes("SKILL.md") ? "SKILL.md" : (result.files[0] ?? null));
      })
      .catch(() => !cancelled && setFiles([]));
    return () => {
      cancelled = true;
    };
  }, [skill.name]);

  // Which of coscribe's tools the skill names, from its text files only.
  useEffect(() => {
    if (tab !== "overview" || !files) return;
    let cancelled = false;
    const textFiles = files.filter((path) => PREVIEWABLE.has(extension(path))).slice(0, 40);
    Promise.all([getTools(), ...textFiles.map((path) => getSkillFileContent(skill.name, path))])
      .then(([tools, ...contents]) => {
        if (cancelled) return;
        const text = contents.map((c) => ("content" in c ? c.content : "")).join("\n");
        setMentionedTools(tools.tools.map((t) => t.name).filter((name) => new RegExp(`\\b${name}\\b`).test(text)).sort());
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [tab, files, skill.name]);

  const tree = useMemo(() => buildFileTree(files ?? []), [files]);
  const tabClass = (active: boolean) =>
    `-mb-px border-b-2 px-4 py-2.5 text-[15px] ${
      active ? "border-[var(--fg)] text-[var(--fg)]" : "border-transparent text-[var(--muted)] hover:text-[var(--fg)]"
    }`;

  return (
    <div className="flex h-full min-h-0 flex-col gap-5">
      <button type="button" className="flex w-fit items-center gap-2 text-[15px] text-[var(--fg)] hover:opacity-70" onClick={onBack}>
        <ArrowLeftIcon className="h-4 w-4" /> Your skills
      </button>
      <div className="flex items-center gap-4">
        <SkillBadge large />
        <div className="min-w-0 flex-1">
          <div className="truncate text-lg font-semibold">{skill.name}</div>
          <div className="text-sm text-[var(--muted)]">
            {SOURCE_BYLINE[skill.source].replace(/^./, (c) => c.toUpperCase())} &middot; updated {shortDate(skill.updated)}
          </div>
        </div>
        <ToggleSwitch on={skill.enabled} onClick={onToggle} label={skill.enabled ? "Turn off" : "Turn on"} />
        {skill.source !== "builtin" && <SkillMenu skill={skill} onToggle={onToggle} onRemove={onRemove} />}
      </div>
      <div className="flex border-b border-[var(--border)]">
        <button type="button" className={tabClass(tab === "overview")} onClick={() => setTab("overview")}>
          Overview
        </button>
        <button type="button" className={tabClass(tab === "contents")} onClick={() => setTab("contents")}>
          Contents{files ? <span className="text-[var(--muted)]"> &middot; {files.length}</span> : null}
        </button>
      </div>

      {tab === "overview" ? (
        <div className="flex flex-col gap-4">
          <p className="text-[15px] leading-relaxed">{skill.description}</p>
          {skill.source === "anthropic" && (
            <p className="text-sm text-[var(--muted)]">
              From github.com/anthropics/skills, under the Apache License 2.0 (see LICENSE.txt in Contents).
            </p>
          )}
          {mentionedTools.length > 0 && (
            <div>
              <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-[var(--muted)]">coscribe tools it uses</div>
              <div className="flex flex-wrap gap-1.5">
                {mentionedTools.map((name) => (
                  <code key={name} className="rounded bg-[var(--card-bg)] px-1.5 py-0.5 font-mono text-xs">
                    {name}
                  </code>
                ))}
              </div>
            </div>
          )}
        </div>
      ) : files === null ? (
        <p className="py-6 text-center text-sm text-[var(--muted)]">Loading...</p>
      ) : (
        <div className="flex min-h-[420px] flex-1 overflow-hidden rounded-xl border border-[var(--border)]">
          <div className="w-60 shrink-0 overflow-y-auto border-r border-[var(--border)] bg-[var(--card-bg)]/40 p-2">
            <FileTreeView
              node={tree}
              prefix=""
              depth={0}
              selectedPath={selected}
              collapsed={collapsed}
              onToggleDir={(path) =>
                setCollapsed((prev) => {
                  const next = new Set(prev);
                  if (next.has(path)) next.delete(path);
                  else next.add(path);
                  return next;
                })
              }
              onSelectFile={setSelected}
            />
          </div>
          {selected ? (
            <FileViewer skillName={skill.name} path={selected} />
          ) : (
            <p className="p-5 text-sm text-[var(--muted)]">No files.</p>
          )}
        </div>
      )}
    </div>
  );
}

function DiscoverRow({ entry, adding, onAdd }: { entry: CatalogSkill; adding: boolean; onAdd: () => void }) {
  return (
    <div className="flex items-center gap-4 border-b border-[var(--border)] py-3.5 last:border-b-0">
      <SkillBadge />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[15px]">{entry.name}</div>
        <div className="truncate text-sm text-[var(--muted)]" title={entry.description}>
          by Anthropic &middot; {entry.description}
        </div>
      </div>
      {entry.added ? (
        <span className="flex shrink-0 items-center gap-1 px-3 text-sm text-[var(--muted)]">
          <CheckIcon className="h-4 w-4" /> Added
        </span>
      ) : (
        <button
          type="button"
          disabled={adding}
          className="shrink-0 rounded-lg border border-[var(--border)] px-4 py-1.5 text-sm hover:bg-[var(--card-bg)] disabled:opacity-60"
          onClick={onAdd}
        >
          {adding ? "Adding..." : "Add"}
        </button>
      )}
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
        <ArrowLeftIcon className="h-3.5 w-3.5" /> Your skills
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


/** Settings > Skills: your skills and the ones built in (Yours), and
 * Anthropic's open skills to add (Discover). Every skill is a folder with a
 * SKILL.md; switching one off stops it being offered in any conversation. */
export function SkillsTab({ active, onCreateSkill }: SkillsTabProps) {
  const { data: skills, status, error, retry } = useFetchOnActive(active, getSkills, []);
  const [view, setView] = useState<View>({ kind: "list" });
  const [tab, setTab] = useState<Tab>("yours");
  const [search, setSearch] = useState("");
  const [catalog, setCatalog] = useState<CatalogSkill[] | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [adding, setAdding] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<SkillInfo | null>(null);
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const addMenuRef = useRef<HTMLDivElement>(null);
  useClickOutside(addMenuRef, () => setAddMenuOpen(false), addMenuOpen);

  const loadCatalog = () => {
    setCatalogError(null);
    getSkillCatalog()
      .then(setCatalog)
      .catch((err: unknown) => setCatalogError(err instanceof Error ? err.message : "Couldn't load Discover."));
  };

  useEffect(() => {
    if (active && tab === "discover" && catalog === null) loadCatalog();
  }, [active, tab, catalog]);

  const toggle = async (skill: SkillInfo) => {
    setActionError(null);
    const result = await setSkillEnabled(skill.name, !skill.enabled);
    if ("error" in result) setActionError(result.error);
    retry();
  };

  const confirmRemove = async () => {
    if (!removeTarget) return;
    const target = removeTarget;
    setRemoveTarget(null);
    setActionError(null);
    const result = await removeSkill(target.name);
    if ("error" in result) setActionError(result.error);
    setView({ kind: "list" });
    setCatalog(null);
    retry();
  };

  const add = async (name: string) => {
    setAdding(name);
    setActionError(null);
    const result = await addCatalogSkill(name);
    setAdding(null);
    if ("error" in result) setActionError(result.error);
    loadCatalog();
    retry();
  };

  const removeDialog = removeTarget && (
    <ConfirmDialog
      title={`Remove ${removeTarget.name}?`}
      description={
        removeTarget.source === "anthropic"
          ? "Its folder is deleted. You can add it again from Discover."
          : "Its folder is deleted from your skills directory. This can't be undone."
      }
      confirmLabel="Remove"
      onCancel={() => setRemoveTarget(null)}
      onConfirm={confirmRemove}
    />
  );

  if (view.kind === "upload") {
    return (
      <UploadSkillView
        onBack={() => setView({ kind: "list" })}
        onUploaded={() => {
          setView({ kind: "list" });
          setTab("yours");
          retry();
        }}
      />
    );
  }

  const detailSkill = view.kind === "detail" ? skills.find((s) => s.name === view.name) : undefined;
  if (detailSkill) {
    return (
      <>
        <SkillDetailView
          skill={detailSkill}
          onBack={() => setView({ kind: "list" })}
          onToggle={() => toggle(detailSkill)}
          onRemove={() => setRemoveTarget(detailSkill)}
        />
        {removeDialog}
      </>
    );
  }

  const segment = (active: boolean) =>
    `rounded-md px-3.5 py-1 text-[15px] ${
      active ? "bg-[var(--bg)] text-[var(--fg)] shadow-sm ring-1 ring-[var(--border)]" : "text-[var(--muted)] hover:text-[var(--fg)]"
    }`;
  const visible = skills.filter((s) => matches(search, s.name, s.description));
  const discover = (catalog ?? []).filter((e) => matches(search, e.name, e.description));

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="mr-1 text-[22px] font-semibold">Skills</h2>
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
            aria-label="Search skills"
            className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--muted)]"
            placeholder="Search skills"
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
              className="absolute right-0 top-full z-20 mt-1 min-w-48 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] py-1 shadow-[var(--shadow)]"
            >
              <button
                type="button"
                role="menuitem"
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]"
                onClick={() => {
                  setAddMenuOpen(false);
                  setView({ kind: "upload" });
                }}
              >
                <UploadIcon className="h-3.5 w-3.5" /> Upload a skill
              </button>
              <button
                type="button"
                role="menuitem"
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--card-bg)]"
                onClick={() => {
                  setAddMenuOpen(false);
                  onCreateSkill();
                }}
              >
                <PencilIcon className="h-3.5 w-3.5" /> Create with coscribe
              </button>
            </div>
          )}
        </div>
      </div>

      {actionError && (
        <p role="alert" className="text-sm text-[var(--danger)]">
          {actionError}
        </p>
      )}

      {tab === "yours" ? (
        <>
          <FetchRetry status={status} error={error} onRetry={retry} />
          {status === "success" &&
            SECTIONS.map(({ source, title }) => {
              const rows = visible.filter((s) => s.source === source);
              if (rows.length === 0) return null;
              return (
                <section key={source}>
                  <h3 className="flex items-center gap-2 text-[17px] font-medium">
                    {title} <CountBadge count={rows.length} />
                  </h3>
                  <div className="mt-1">
                    {rows.map((skill) => (
                      <SkillRow
                        key={skill.name}
                        skill={skill}
                        onOpen={() => setView({ kind: "detail", name: skill.name })}
                        onToggle={() => toggle(skill)}
                        onRemove={() => setRemoveTarget(skill)}
                      />
                    ))}
                  </div>
                </section>
              );
            })}
          {status === "success" && visible.length === 0 && (
            <p className="py-6 text-center text-sm text-[var(--muted)]">
              {skills.length === 0 ? "No skills yet." : `No skills match "${search.trim()}".`}
            </p>
          )}
        </>
      ) : (
        <div>
          {catalogError && (
            <p className="text-sm text-[var(--danger)]">
              Couldn't load Discover: {catalogError} --{" "}
              <button type="button" className="underline" onClick={loadCatalog}>
                Retry
              </button>
            </p>
          )}
          {catalog === null && !catalogError && <p className="text-sm text-[var(--muted)]">Loading...</p>}
          {discover.map((entry) => (
            <DiscoverRow key={entry.name} entry={entry} adding={adding === entry.name} onAdd={() => add(entry.name)} />
          ))}
          {catalog !== null && discover.length === 0 && (
            <p className="py-6 text-center text-sm text-[var(--muted)]">No skills match "{search.trim()}".</p>
          )}
          {catalog !== null && (
            <p className="mt-4 text-xs text-[var(--muted)]">
              Open-source skills from github.com/anthropics/skills (Apache License 2.0). Adding one downloads its folder.
            </p>
          )}
        </div>
      )}
      {removeDialog}
    </div>
  );
}
