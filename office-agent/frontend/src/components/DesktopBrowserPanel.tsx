import { useEffect, useRef, useState } from "react";
import {
  CloseIcon,
  CursorClickIcon,
  ExpandIcon,
  ExternalLinkIcon,
  GlobeIcon,
  MoreIcon,
  PlusIcon,
  ReloadIcon,
  ShrinkIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  PencilIcon,
  StopIcon,
} from "./icons";
import { AllowedSitesDialog } from "./AllowedSitesDialog";
import { BrowserAnnotator, type PageCapture } from "./BrowserAnnotator";
import {
  browserPanelBack,
  browserPanelCapture,
  browserPanelClose,
  browserPanelCloseTab,
  browserPanelForward,
  browserPanelNavigate,
  browserPanelNewTab,
  browserPanelOpen,
  browserPanelOpenExternal,
  browserPanelReload,
  browserPanelReposition,
  browserPanelSelectTab,
  browserPanelSetPickMode,
  browserPanelSetViewHidden,
  browserPanelShowMenu,
  onBrowserAgent,
  onBrowserPanelPicked,
  onBrowserPanelTabs,
  type BrowserPanelRect,
  type BrowserTab,
  answerBrowserPermission,
  onBrowserPermission,
  onShowAllowedSites,
  type BrowserPermissionAnswer,
  type BrowserPermissionRequest,
} from "../lib/electron";
import { useBackdropOpen } from "../lib/titleBar";
import { PickedPreview, type BrowserCapture, type PickedElement } from "./PickedPreview";
import { usePanelWidth } from "../lib/usePanelWidth";

const iconButton =
  "flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)] disabled:opacity-35 disabled:hover:bg-transparent";

/** What the address bar shows while not being edited: the page's host
 * and path, without the scheme, as in Chrome's compact omnibox. */
function displayUrl(url: string): string {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return url;
    const path = parsed.pathname === "/" ? "" : parsed.pathname;
    return `${parsed.host}${path}${parsed.search}`;
  } catch {
    return url;
  }
}

function TabButton({
  tab,
  active,
  onSelect,
  onClose,
}: {
  tab: BrowserTab;
  active: boolean;
  onSelect: () => void;
  onClose: () => void;
}) {
  const [faviconFailed, setFaviconFailed] = useState(false);
  useEffect(() => setFaviconFailed(false), [tab.favicon]);
  return (
    <div
      role="tab"
      aria-selected={active}
      title={tab.url || tab.title}
      className={`group flex h-7 min-w-0 max-w-[180px] shrink cursor-default items-center gap-1.5 rounded-lg border pl-2 pr-1 text-[13px] ${
        active
          ? "border-[var(--border-hover)] bg-[var(--bg)] text-[var(--fg)]"
          : "border-transparent text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
      }`}
      onMouseDown={(e) => {
        // Middle-click closes, as in a browser.
        if (e.button === 1) {
          e.preventDefault();
          onClose();
        }
      }}
      onClick={onSelect}
    >
      {tab.loading ? (
        <span className="h-3 w-3 shrink-0 animate-spin rounded-full border-[1.5px] border-[var(--muted)] border-t-transparent" />
      ) : tab.favicon && !faviconFailed ? (
        <img src={tab.favicon} alt="" className="h-3.5 w-3.5 shrink-0" onError={() => setFaviconFailed(true)} />
      ) : (
        <GlobeIcon className="h-3.5 w-3.5 shrink-0" />
      )}
      <span className="min-w-0 flex-1 truncate">{tab.title || "New tab"}</span>
      <button
        type="button"
        aria-label={`Close ${tab.title || "tab"}`}
        className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
      >
        <CloseIcon className="h-3 w-3" />
      </button>
    </div>
  );
}

/** The desktop app's Browser panel: real tabs (native views the desktop
 * app lays over the content area below), shared with coscribe's AI,
 * which drives them through the browser_* tools while the user watches. */
export function DesktopBrowserPanel({
  threadId,
  onClose,
  onSendToChat,
  onStopAgent,
}: {
  threadId: string;
  onClose: () => void;
  onSendToChat: (capture: BrowserCapture) => void;
  onStopAgent: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const addressRef = useRef<HTMLInputElement>(null);
  const [tabs, setTabs] = useState<BrowserTab[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [address, setAddress] = useState("");
  const [editingAddress, setEditingAddress] = useState(false);
  const [pickMode, setPickMode] = useState(false);
  const [picked, setPicked] = useState<PickedElement | null>(null);
  const [agent, setAgent] = useState<{ threadId: string; busy: boolean } | null>(null);
  const [annotation, setAnnotation] = useState<PageCapture | null>(null);
  const [permission, setPermission] = useState<BrowserPermissionRequest | null>(null);
  const [allowedSitesOpen, setAllowedSitesOpen] = useState(false);
  const { width, expanded, toggleExpanded, onResizeHandleMouseDown } = usePanelWidth();

  const active = tabs.find((t) => t.id === activeId) ?? null;

  useEffect(() => {
    if (!editingAddress) setAddress(active?.url ?? "");
  }, [active?.url, editingAddress]);

  // The native view is laid over containerRef's box; it moves whenever
  // that box does -- the window resizing, the panel being dragged wider,
  // the sidebar opening.
  useEffect(() => {
    let cancelled = false;
    let observer: ResizeObserver | undefined;
    const rect = (): BrowserPanelRect | null => {
      const box = containerRef.current?.getBoundingClientRect();
      if (!box) return null;
      return { x: Math.round(box.left), y: Math.round(box.top), width: Math.round(box.width), height: Math.round(box.height) };
    };
    const reposition = () => {
      const next = rect();
      if (next && !cancelled) void browserPanelReposition(next);
    };
    const stopTabs = onBrowserPanelTabs((payload) => {
      setTabs(payload.tabs);
      setActiveId(payload.activeId);
    });
    const stopPicked = onBrowserPanelPicked((payload) => {
      setPicked(payload);
      setPickMode(false);
    });
    (async () => {
      const first = rect();
      if (!first || cancelled) return;
      await browserPanelOpen(first);
      if (cancelled) return;
      window.addEventListener("resize", reposition);
      if (containerRef.current) {
        observer = new ResizeObserver(reposition);
        observer.observe(containerRef.current);
      }
    })();
    return () => {
      cancelled = true;
      stopTabs();
      stopPicked();
      window.removeEventListener("resize", reposition);
      observer?.disconnect();
      void browserPanelClose();
    };
  }, []);

  useEffect(() => {
    void browserPanelSetPickMode(pickMode);
  }, [pickMode]);

  useEffect(() => {
    // Steps arrive a few seconds apart while the model thinks, so "busy"
    // lingers between them rather than blinking off after each one.
    let idle: ReturnType<typeof setTimeout> | undefined;
    const stop = onBrowserAgent((payload) => {
      if (!payload.threadId) return;
      if (payload.busy) {
        clearTimeout(idle);
        setAgent({ threadId: payload.threadId, busy: true });
      } else if (payload.busy === false) {
        clearTimeout(idle);
        idle = setTimeout(() => setAgent(null), 6000);
      }
    });
    return () => {
      clearTimeout(idle);
      stop();
    };
  }, []);

  const startAnnotating = async () => {
    const capture = await browserPanelCapture();
    if (!capture) return;
    setPickMode(false);
    setAnnotation(capture);
  };
  const stopAnnotating = () => setAnnotation(null);

  // The live page is a native view drawn over the window; while the
  // drawing layer or any dialog needs that spot, the view steps aside.
  const backdropOpen = useBackdropOpen();
  const viewHidden = annotation !== null || backdropOpen;
  useEffect(() => {
    void browserPanelSetViewHidden(viewHidden);
  }, [viewHidden, agent]);

  // The drawing is of one page at one moment: switching tabs or the AI
  // acting on the page ends it.
  useEffect(() => setAnnotation(null), [activeId]);
  useEffect(() => {
    if (agent?.busy) setAnnotation(null);
  }, [agent]);

  useEffect(() => onBrowserPermission(setPermission), []);
  useEffect(() => onShowAllowedSites(() => setAllowedSitesOpen(true)), []);

  const answerPermission = (answer: BrowserPermissionAnswer) => {
    if (!permission) return;
    void answerBrowserPermission(permission.requestId, answer);
    setPermission(null);
  };
  const stopAgent = () => {
    answerPermission("deny");
    onStopAgent();
  };

  const go = () => {
    const url = address.trim();
    if (!url) return;
    void browserPanelNavigate(url);
    setEditingAddress(false);
    addressRef.current?.blur();
  };

  const showEmptyState = !active || !active.url;

  return (
    <aside
      aria-label="Browser"
      className="relative my-2 mr-2 flex shrink-0 flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--panel-bg)]"
      style={{ width }}
    >
      <div
        onMouseDown={onResizeHandleMouseDown}
        title="Drag to resize"
        className="absolute left-0 top-0 z-10 h-full w-1.5 cursor-col-resize hover:bg-[var(--accent)]/30"
      />

      <div className="flex h-11 items-center gap-1 px-2">
        <div role="tablist" aria-label="Tabs" className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden">
          {tabs.map((tab) => (
            <TabButton
              key={tab.id}
              tab={tab}
              active={tab.id === activeId}
              onSelect={() => void browserPanelSelectTab(tab.id)}
              onClose={() => void browserPanelCloseTab(tab.id)}
            />
          ))}
          <button type="button" title="New tab" aria-label="New tab" className={iconButton} onClick={() => void browserPanelNewTab()}>
            <PlusIcon className="h-4 w-4" />
          </button>
        </div>
        <button
          type="button"
          title="More"
          aria-label="More browser actions"
          aria-haspopup="menu"
          className={iconButton}
          onClick={(e) => {
            // A native menu: a DOM one would open underneath the page view.
            const box = e.currentTarget.getBoundingClientRect();
            browserPanelShowMenu(box.left, box.bottom + 4);
          }}
        >
          <MoreIcon className="h-4 w-4" />
        </button>
        <button
          type="button"
          title={expanded ? "Restore size" : "Expand"}
          aria-label={expanded ? "Restore size" : "Expand"}
          className={iconButton}
          onClick={toggleExpanded}
        >
          {expanded ? <ShrinkIcon className="h-4 w-4" /> : <ExpandIcon className="h-4 w-4" />}
        </button>
        <button type="button" title="Close" aria-label="Close browser" className={iconButton} onClick={onClose}>
          <CloseIcon className="h-4 w-4" />
        </button>
      </div>

      <div className="flex h-10 items-center gap-0.5 px-2 pb-1.5">
        <button type="button" title="Back" aria-label="Back" disabled={!active?.canGoBack} className={iconButton} onClick={() => void browserPanelBack()}>
          <ArrowLeftIcon className="h-4 w-4" />
        </button>
        <button
          type="button"
          title="Forward"
          aria-label="Forward"
          disabled={!active?.canGoForward}
          className={iconButton}
          onClick={() => void browserPanelForward()}
        >
          <ArrowRightIcon className="h-4 w-4" />
        </button>
        <button type="button" title="Reload" aria-label="Reload" disabled={!active?.url} className={iconButton} onClick={() => void browserPanelReload()}>
          <ReloadIcon className="h-[15px] w-[15px]" />
        </button>
        <div className="mx-1 flex h-8 min-w-0 flex-1 items-center rounded-lg border border-[var(--border)] bg-[var(--bg)] focus-within:border-[var(--border-hover)]">
          <input
            ref={addressRef}
            aria-label="Address"
            className={`h-full min-w-0 flex-1 bg-transparent px-2.5 text-[13px] outline-none placeholder:text-[var(--muted)] ${editingAddress ? "text-left" : "text-center"}`}
            placeholder="Type a URL"
            spellCheck={false}
            value={editingAddress ? address : displayUrl(address)}
            onFocus={(e) => {
              setEditingAddress(true);
              requestAnimationFrame(() => e.target.select());
            }}
            onBlur={() => setEditingAddress(false)}
            onChange={(e) => setAddress(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") go();
              if (e.key === "Escape") {
                setAddress(active?.url ?? "");
                addressRef.current?.blur();
              }
            }}
          />
          <button
            type="button"
            title="Open in your browser"
            aria-label="Open in your browser"
            disabled={!active?.url}
            className="mr-1 flex h-6 w-6 shrink-0 items-center justify-center rounded text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)] disabled:opacity-35"
            onClick={() => void browserPanelOpenExternal()}
          >
            <ExternalLinkIcon className="h-3.5 w-3.5" />
          </button>
        </div>
        <button
          type="button"
          title="Annotate this page and send it to the chat"
          aria-label="Annotate"
          aria-pressed={annotation !== null}
          disabled={!active?.url}
          className={`${iconButton} ${annotation ? "!bg-blue-500/15 !text-blue-600" : ""}`}
          onClick={() => (annotation ? stopAnnotating() : void startAnnotating())}
        >
          <PencilIcon className="h-[15px] w-[15px]" />
        </button>
        <button
          type="button"
          title="Select an element to send to the chat"
          aria-label="Select element"
          aria-pressed={pickMode}
          disabled={!active?.url}
          className={`${iconButton} ${pickMode ? "!bg-[var(--accent)]/15 !text-[var(--accent)]" : ""}`}
          onClick={() => {
            if (annotation) stopAnnotating();
            setPickMode((v) => !v);
          }}
        >
          <CursorClickIcon className="h-4 w-4" />
        </button>
      </div>

      {agent && (
        <div className="mx-2 mb-1.5 flex items-center gap-2 rounded-lg bg-[var(--accent)]/10 px-2.5 py-1.5 text-xs text-[var(--fg)]">
          <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-[var(--accent)]" />
          <span className="min-w-0 flex-1 truncate">
            {agent.threadId === threadId ? "coscribe is using the browser" : "coscribe is using the browser in another conversation"}
          </span>
          {agent.threadId === threadId && (
            <button
              type="button"
              className="flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 font-medium hover:bg-[var(--accent)]/15"
              onClick={stopAgent}
            >
              <StopIcon className="h-3 w-3" /> Stop
            </button>
          )}
        </div>
      )}

      {permission && (
        <div
          role="alertdialog"
          aria-label="Site permission"
          className="mx-2 mb-1.5 flex flex-col gap-2.5 rounded-xl border border-[var(--border)] bg-[var(--bg)] p-3 shadow-[var(--shadow)]"
        >
          <div className="flex items-start gap-2.5">
            <GlobeIcon className="mt-0.5 h-4 w-4 shrink-0 text-[var(--muted)]" />
            <div className="min-w-0 text-sm">
              <p className="font-medium">
                Allow coscribe to use <span className="break-all">{permission.host}</span>?
              </p>
              <p className="mt-0.5 text-[13px] text-[var(--muted)]">
                {permission.threadId === threadId
                  ? "It will read and act on this site in the Browser panel."
                  : "Asked from another conversation. It will read and act on this site in the Browser panel."}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap justify-end gap-1.5">
            <button
              type="button"
              className="h-7 rounded-lg border border-[var(--border)] px-2.5 text-[13px] hover:bg-[var(--card-bg)]"
              onClick={() => answerPermission("deny")}
            >
              Don't allow
            </button>
            <button
              type="button"
              className="h-7 rounded-lg border border-[var(--border)] px-2.5 text-[13px] hover:bg-[var(--card-bg)]"
              onClick={() => answerPermission("once")}
            >
              Allow for this chat
            </button>
            <button
              type="button"
              className="h-7 rounded-lg bg-[var(--primary)] px-2.5 text-[13px] font-medium text-[var(--primary-fg)] hover:bg-[var(--primary-hover)]"
              onClick={() => answerPermission("always")}
            >
              Always allow
            </button>
          </div>
        </div>
      )}

      {active?.loadError && (
        <div className="mx-2 mb-1.5 rounded-lg bg-red-500/10 px-2.5 py-1.5 text-xs text-red-500">{active.loadError}</div>
      )}

      <div className="flex min-h-0 flex-1 flex-col px-1.5 pb-1.5">
        <div ref={containerRef} data-testid="browser-page-area" className="relative min-h-0 flex-1 overflow-hidden rounded-lg bg-[var(--bg)]">
          {annotation && <BrowserAnnotator capture={annotation} onClose={stopAnnotating} onSendToChat={onSendToChat} />}
          {showEmptyState && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 px-6 text-center">
              <GlobeIcon className="mb-1 h-5 w-5 text-[var(--muted)]" />
              <p className="text-[15px] text-[var(--fg)]">Browse with coscribe</p>
              <p className="text-sm leading-snug text-[var(--muted)]">
                Type a URL or ask coscribe to open a site.
                <br />
                coscribe can read, click, and type.
              </p>
            </div>
          )}
        </div>
      </div>

      {allowedSitesOpen && <AllowedSitesDialog onClose={() => setAllowedSitesOpen(false)} />}
      {picked && <PickedPreview picked={picked} onDiscard={() => setPicked(null)} onSendToChat={onSendToChat} />}
    </aside>
  );
}
