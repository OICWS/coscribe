import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  deleteThreadGroup,
  getThreadGroups,
  getThreadStatuses,
  renameThread,
  renameThreadGroup,
  setThreadMeta,
} from "../lib/rest";
import { goToThread } from "../lib/nav";
import { readStored, writeStored } from "../lib/storage";
import { FILTERS, matchesFilter, sectionThreads, STATUS_LABEL, type ThreadFilter } from "../lib/threadStatus";
import { useClickOutside } from "../lib/useClickOutside";
import type { ThreadStatus, ThreadSummary } from "../types/session";
import {
  ArchiveIcon,
  CheckCircleIcon,
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  EyeIcon,
  FilterIcon,
  FolderIcon,
  FolderPlusIcon,
  HandIcon,
  ListChecksIcon,
  MoreIcon,
  PencilIcon,
  ReloadIcon,
  SearchIcon,
  TrashIcon,
} from "./icons";
import { ThreadStatusIcon } from "./ThreadStatusIcon";

const COLLAPSED_KEY = "coscribe.collapsedGroups";
// A type of our own, not text/plain, so the composer and other drop
// targets ignore a dragged conversation.
const THREAD_DRAG_TYPE = "application/x-coscribe-thread";
const STATUS_POLL_MS = 3000;
const MENU_WIDTH = 208;

const menuClass =
  "z-50 rounded-[10px] border border-[var(--border)] bg-[var(--panel-bg)] py-1 text-sm shadow-[var(--shadow)]";
const itemClass = "flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-[var(--card-bg)]";

function readCollapsed(): Set<string> {
  try {
    const parsed: unknown = JSON.parse(readStored(COLLAPSED_KEY) ?? "[]");
    return new Set(Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : []);
  } catch {
    return new Set();
  }
}

interface MenuState {
  thread: ThreadSummary;
  x: number;
  y: number;
}

/** The menu for one conversation, at the pointer for a right-click or under
 * its "..." button. */
function ThreadMenu({
  menu,
  groups,
  isCurrent,
  onClose,
  onRename,
  onMove,
  onArchive,
  onDelete,
}: {
  menu: MenuState;
  groups: string[];
  isCurrent: boolean;
  onClose: () => void;
  onRename: () => void;
  onMove: (group: string) => void;
  onArchive: () => void;
  onDelete: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [submenu, setSubmenu] = useState(false);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  useClickOutside(ref, onClose, true);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const left = Math.max(4, Math.min(menu.x, window.innerWidth - MENU_WIDTH - 4));
  const top = Math.max(4, Math.min(menu.y, window.innerHeight - 220));
  const flip = left + MENU_WIDTH * 2 + 8 > window.innerWidth;
  const current = menu.thread.group;
  const commitName = () => {
    const trimmed = name.trim();
    if (trimmed) onMove(trimmed);
  };

  return createPortal(
    <div
      ref={ref}
      role="menu"
      aria-label={`Options for ${menu.thread.preview || menu.thread.thread_id}`}
      className={`${menuClass} fixed`}
      style={{ left, top, width: MENU_WIDTH }}
      onClick={(e) => e.stopPropagation()}
    >
      <button type="button" role="menuitem" className={itemClass} onClick={onRename}>
        <PencilIcon className="h-3.5 w-3.5" /> Rename
      </button>
      <div className="relative" onMouseEnter={() => setSubmenu(true)}>
        <button
          type="button"
          role="menuitem"
          aria-haspopup="menu"
          aria-expanded={submenu}
          className={`${itemClass} justify-between`}
          onClick={() => setSubmenu(true)}
        >
          <span className="flex items-center gap-2">
            <FolderIcon className="h-3.5 w-3.5" /> Move to group
          </span>
          <ChevronRightIcon className="h-3.5 w-3.5 text-[var(--muted)]" />
        </button>
        {submenu && (
          <div
            role="menu"
            aria-label="Move to group"
            className={`${menuClass} absolute top-[-5px] ${flip ? "right-full mr-1" : "left-full ml-1"}`}
            style={{ width: MENU_WIDTH }}
          >
            {groups.map((group) => (
              <button
                key={group}
                type="button"
                role="menuitemradio"
                aria-checked={current === group}
                className={`${itemClass} justify-between`}
                onClick={() => onMove(group)}
              >
                <span className="min-w-0 truncate">{group}</span>
                {current === group && <CheckIcon className="h-3.5 w-3.5 shrink-0" />}
              </button>
            ))}
            {groups.length > 0 && <div className="my-1 border-t border-[var(--border)]" />}
            <button
              type="button"
              role="menuitemradio"
              aria-checked={!current}
              className={`${itemClass} justify-between`}
              onClick={() => onMove("")}
            >
              Ungrouped
              {!current && <CheckIcon className="h-3.5 w-3.5 shrink-0" />}
            </button>
            {naming ? (
              <div className="px-2 py-1">
                <input
                  autoFocus
                  aria-label="New group name"
                  placeholder="Group name"
                  className="w-full rounded-md border border-[var(--accent)] bg-[var(--bg)] px-2 py-1 text-sm outline-none"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      commitName();
                    }
                  }}
                />
              </div>
            ) : (
              <button type="button" role="menuitem" className={itemClass} onClick={() => setNaming(true)}>
                <FolderPlusIcon className="h-3.5 w-3.5" /> New group…
              </button>
            )}
          </div>
        )}
      </div>
      <div className="my-1 border-t border-[var(--border)]" onMouseEnter={() => setSubmenu(false)} />
      <button
        type="button"
        role="menuitem"
        className={itemClass}
        onMouseEnter={() => setSubmenu(false)}
        onClick={onArchive}
      >
        <ArchiveIcon className="h-3.5 w-3.5" /> {menu.thread.archived ? "Unarchive" : "Archive"}
      </button>
      {!isCurrent && (
        <button
          type="button"
          role="menuitem"
          className={`${itemClass} text-red-500`}
          onMouseEnter={() => setSubmenu(false)}
          onClick={onDelete}
        >
          <TrashIcon className="h-3.5 w-3.5" /> Delete
        </button>
      )}
    </div>,
    document.body,
  );
}

function ThreadRow({
  thread,
  isCurrent,
  renaming,
  draggable,
  onOpenMenu,
  onRenameDone,
  onDragStart,
  onDragEnd,
}: {
  thread: ThreadSummary;
  isCurrent: boolean;
  renaming: boolean;
  draggable: boolean;
  onOpenMenu: (thread: ThreadSummary, x: number, y: number) => void;
  onRenameDone: (title: string | null) => void;
  onDragStart: () => void;
  onDragEnd: () => void;
}) {
  const [value, setValue] = useState(thread.preview);
  const title = thread.preview || thread.thread_id;

  if (renaming) {
    return (
      <input
        autoFocus
        aria-label="Session name"
        className="w-full min-w-0 rounded-md border border-[var(--accent)] bg-[var(--card-bg)] px-2 py-1 text-sm outline-none"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => onRenameDone(value)}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            onRenameDone(value);
          } else if (e.key === "Escape") {
            onRenameDone(null);
          }
        }}
      />
    );
  }

  return (
    <div
      data-testid="thread-row"
      data-status={thread.status}
      className={`group flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-[var(--card-bg)] ${
        isCurrent ? "bg-[var(--card-bg)] font-medium" : "cursor-pointer"
      }`}
      title={`${title} · ${STATUS_LABEL[thread.status]}`}
      draggable={draggable}
      onDragStart={(e) => {
        e.dataTransfer.setData(THREAD_DRAG_TYPE, thread.thread_id);
        e.dataTransfer.effectAllowed = "move";
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      onClick={() => !isCurrent && goToThread(thread.thread_id)}
      onContextMenu={(e) => {
        e.preventDefault();
        onOpenMenu(thread, e.clientX, e.clientY);
      }}
    >
      <ThreadStatusIcon status={thread.status} />
      <span className={`min-w-0 flex-1 truncate ${thread.archived ? "text-[var(--muted)]" : ""}`}>{title}</span>
      <button
        type="button"
        aria-label={`Options for ${title}`}
        className="rounded-md p-1 text-[var(--muted)] opacity-0 hover:bg-[var(--border)] focus-visible:opacity-100 group-hover:opacity-100"
        onClick={(e) => {
          e.stopPropagation();
          const box = e.currentTarget.getBoundingClientRect();
          onOpenMenu(thread, box.left, box.bottom + 2);
        }}
      >
        <MoreIcon className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function GroupHeader({
  name,
  count,
  collapsed,
  onToggle,
  onRenamed,
  onDeleted,
}: {
  name: string | null;
  count: number;
  collapsed: boolean;
  onToggle: () => void;
  onRenamed: (from: string, to: string) => void;
  onDeleted: (name: string) => void;
}) {
  const [menu, setMenu] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [value, setValue] = useState(name ?? "");
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(ref, () => setMenu(false), menu);

  const commit = async () => {
    const next = value.trim();
    if (!name || !next || next === name) {
      setRenaming(false);
      setValue(name ?? "");
      return;
    }
    const result = await renameThreadGroup(name, next);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    setRenaming(false);
    setError(null);
    onRenamed(name, result.name);
  };

  if (renaming && name) {
    return (
      <div className="px-1 py-1">
        <input
          autoFocus
          aria-label="Group name"
          className="w-full rounded-md border border-[var(--accent)] bg-[var(--card-bg)] px-2 py-1 text-sm outline-none"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onBlur={() => !error && commit()}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void commit();
            } else if (e.key === "Escape") {
              setRenaming(false);
              setError(null);
            }
          }}
        />
        {error && <p className="mt-1 text-xs text-[var(--danger)]">{error}</p>}
      </div>
    );
  }

  return (
    <div className="group/header relative flex items-center rounded-md hover:bg-[var(--card-bg)]" ref={ref}>
      <button
        type="button"
        aria-expanded={!collapsed}
        className="flex min-w-0 flex-1 items-center gap-1.5 px-2 py-1 text-left text-xs font-medium text-[var(--muted)]"
        onClick={onToggle}
      >
        {collapsed ? <ChevronRightIcon className="h-3 w-3 shrink-0" /> : <ChevronDownIcon className="h-3 w-3 shrink-0" />}
        <span className="truncate">{name ?? "Ungrouped"}</span>
        <span className="font-normal opacity-70">{count}</span>
      </button>
      {name && (
        <>
          <button
            type="button"
            aria-label={`Options for group ${name}`}
            className="mr-1 rounded-md p-1 text-[var(--muted)] opacity-0 hover:bg-[var(--border)] focus-visible:opacity-100 group-hover/header:opacity-100"
            onClick={() => setMenu((v) => !v)}
          >
            <MoreIcon className="h-3.5 w-3.5" />
          </button>
          {menu && (
            <div className={`${menuClass} absolute right-1 top-full w-40`}>
              <button
                type="button"
                className={itemClass}
                onClick={() => {
                  setMenu(false);
                  setValue(name);
                  setRenaming(true);
                }}
              >
                <PencilIcon className="h-3.5 w-3.5" /> Rename group
              </button>
              <button
                type="button"
                className={`${itemClass} text-red-500`}
                onClick={() => {
                  setMenu(false);
                  onDeleted(name);
                }}
              >
                <TrashIcon className="h-3.5 w-3.5" /> Delete group
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

const FILTER_ICON: Record<ThreadFilter, ReactNode> = {
  all: <ListChecksIcon className="h-4 w-4" />,
  needs_input: <HandIcon className="h-4 w-4" />,
  ready: <EyeIcon className="h-4 w-4" />,
  working: <ReloadIcon className="h-4 w-4" />,
  idle: <CheckCircleIcon className="h-4 w-4" />,
  archived: <ArchiveIcon className="h-4 w-4" />,
};

export interface ThreadListProps {
  threads: ThreadSummary[];
  currentId: string;
  onChanged: () => void;
  onRenamed: (threadId: string, title: string) => void;
  onDeleteRequest: (thread: ThreadSummary) => void;
}

/** The conversations in the sidebar: grouped, each with its status, with a
 * filter by status (Archived among them) and a text search. */
export function ThreadList({ threads, currentId, onChanged, onRenamed, onDeleteRequest }: ThreadListProps) {
  const [groups, setGroups] = useState<string[]>([]);
  const [statuses, setStatuses] = useState<Record<string, ThreadStatus>>({});
  const [filter, setFilter] = useState<ThreadFilter>("all");
  const [filterOpen, setFilterOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(readCollapsed);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [dragging, setDragging] = useState<ThreadSummary | null>(null);
  const [dropKey, setDropKey] = useState<string | null>(null);
  const filterRef = useRef<HTMLDivElement>(null);
  useClickOutside(filterRef, () => setFilterOpen(false), filterOpen);

  const refreshGroups = useCallback(() => {
    getThreadGroups()
      .then((result) => setGroups(result.groups))
      .catch(() => undefined);
  }, []);
  useEffect(refreshGroups, [refreshGroups]);

  // A conversation's status changes while the list is open (a reply
  // finishes, an approval is asked for), so ask again every few seconds.
  useEffect(() => {
    let current = true;
    const poll = () => {
      getThreadStatuses()
        .then((result) => current && setStatuses(result.statuses))
        .catch(() => undefined);
    };
    poll();
    const timer = window.setInterval(poll, STATUS_POLL_MS);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, []);

  const shown = threads
    .map((t) => ({ ...t, status: statuses[t.thread_id] ?? t.status }))
    .filter((t) => matchesFilter(t, filter, query));
  const filtering = filter !== "all" || query.trim() !== "";
  const sections = sectionThreads(shown, groups, filtering);
  const hasGroups = groups.length > 0;
  const total = threads.filter((t) => !t.archived).length;

  const toggle = (key: string) => {
    const next = new Set(collapsed);
    if (!next.delete(key)) next.add(key);
    setCollapsed(next);
    writeStored(COLLAPSED_KEY, JSON.stringify([...next]));
  };

  const move = async (thread: ThreadSummary, group: string) => {
    setMenu(null);
    const result = await setThreadMeta(thread.thread_id, { group });
    if (!("error" in result)) setGroups(result.groups);
    onChanged();
  };
  const archive = async (thread: ThreadSummary) => {
    setMenu(null);
    await setThreadMeta(thread.thread_id, { archived: !thread.archived });
    onChanged();
  };
  const finishRename = (thread: ThreadSummary, value: string | null) => {
    setRenamingId(null);
    const title = value?.trim();
    if (!title || title === thread.preview) return;
    renameThread(thread.thread_id, title).then((result) => {
      if ("title" in result) onRenamed(thread.thread_id, result.title);
    });
  };
  const deleteGroup = async (name: string) => {
    const result = await deleteThreadGroup(name);
    setGroups(result.groups);
    onChanged();
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-1 mt-2 flex items-center justify-between px-2">
        <div className="text-xs font-medium tracking-wide text-[var(--muted)]">
          {filter === "archived" ? "ARCHIVED" : "RECENTS"}
        </div>
        <div className="flex items-center gap-0.5 text-[var(--muted)]">
          <button
            type="button"
            title="Search sessions"
            aria-pressed={searching}
            className="rounded-md p-1 hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
            onClick={() => {
              setSearching((v) => !v);
              setQuery("");
            }}
          >
            <SearchIcon className="h-3.5 w-3.5" />
          </button>
          <div className="relative" ref={filterRef}>
            <button
              type="button"
              title="Filter sessions"
              aria-haspopup="menu"
              aria-expanded={filterOpen}
              className={`relative rounded-md p-1 hover:bg-[var(--card-bg)] hover:text-[var(--fg)] ${
                filter !== "all" ? "text-[var(--fg)]" : ""
              }`}
              onClick={() => setFilterOpen((v) => !v)}
            >
              <FilterIcon className="h-3.5 w-3.5" />
              {filter !== "all" && (
                <span className="absolute right-0 top-0 h-1.5 w-1.5 rounded-full bg-[var(--accent)]" />
              )}
            </button>
            {filterOpen && (
              <div role="menu" aria-label="Filter sessions" className={`${menuClass} absolute right-0 top-full mt-1 w-48`}>
                {FILTERS.map(({ value, label }) => (
                  <button
                    key={value}
                    type="button"
                    role="menuitemradio"
                    aria-checked={filter === value}
                    className={`${itemClass} text-[var(--fg)]`}
                    onClick={() => {
                      setFilter(value);
                      setFilterOpen(false);
                    }}
                  >
                    <span className="w-4 text-[var(--muted)]">{filter === value ? <CheckIcon className="h-4 w-4" /> : null}</span>
                    <span className="text-[var(--muted)]">{FILTER_ICON[value]}</span>
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
      {searching && (
        <input
          autoFocus
          aria-label="Search sessions"
          placeholder="Search sessions"
          className="mx-1 mb-1 rounded-md border border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-sm outline-none focus:border-[var(--accent)]"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      )}
      <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
        {threads.length === 0 && <div className="px-2 py-1 text-sm text-[var(--muted)]">No sessions yet.</div>}
        {threads.length > 0 && shown.length === 0 && !hasGroups && (
          <div className="px-2 py-1 text-sm text-[var(--muted)]">
            {filter === "archived" ? "Nothing archived." : total === 0 ? "Everything is archived." : "No matches."}
          </div>
        )}
        {sections.map((section) => {
          const key = section.group ?? "";
          const open = !collapsed.has(key);
          // Without any group the list is the plain list it always was.
          const plain = section.group === null && !hasGroups;
          const accepts = !plain && dragging !== null && (dragging.group ?? "") !== key;
          return (
            <div
              key={key || "__ungrouped"}
              data-testid="thread-section"
              data-group={key}
              className={`flex flex-col gap-0.5 rounded-md ${
                accepts && dropKey === key ? "bg-[var(--card-bg)] outline outline-1 outline-[var(--accent)]" : ""
              }`}
              onDragOver={(e) => {
                if (!accepts || !e.dataTransfer.types.includes(THREAD_DRAG_TYPE)) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
                if (dropKey !== key) setDropKey(key);
              }}
              onDragLeave={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node | null) && dropKey === key) setDropKey(null);
              }}
              onDrop={(e) => {
                if (!accepts || !dragging) return;
                e.preventDefault();
                const thread = dragging;
                setDragging(null);
                setDropKey(null);
                void move(thread, key);
              }}
            >
              {!plain && (
                <GroupHeader
                  name={section.group}
                  count={section.threads.length}
                  collapsed={!open}
                  onToggle={() => toggle(key)}
                  onRenamed={() => {
                    refreshGroups();
                    onChanged();
                  }}
                  onDeleted={deleteGroup}
                />
              )}
              {(plain || open) &&
                section.threads.map((thread) => (
                  <ThreadRow
                    key={thread.thread_id}
                    thread={thread}
                    isCurrent={thread.thread_id === currentId}
                    renaming={renamingId === thread.thread_id}
                    draggable={hasGroups}
                    onOpenMenu={(t, x, y) => setMenu({ thread: t, x, y })}
                    onRenameDone={(value) => finishRename(thread, value)}
                    onDragStart={() => setDragging(thread)}
                    onDragEnd={() => {
                      setDragging(null);
                      setDropKey(null);
                    }}
                  />
                ))}
              {!plain && open && section.threads.length === 0 && (
                <div className="px-6 py-0.5 text-xs text-[var(--muted)]">Empty</div>
              )}
            </div>
          );
        })}
      </div>
      {menu && (
        <ThreadMenu
          menu={menu}
          groups={groups}
          isCurrent={menu.thread.thread_id === currentId}
          onClose={() => setMenu(null)}
          onRename={() => {
            setRenamingId(menu.thread.thread_id);
            setMenu(null);
          }}
          onMove={(group) => void move(menu.thread, group)}
          onArchive={() => void archive(menu.thread)}
          onDelete={() => {
            onDeleteRequest(menu.thread);
            setMenu(null);
          }}
        />
      )}
    </div>
  );
}
