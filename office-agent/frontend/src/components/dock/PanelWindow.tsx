import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { isViewable } from "../../lib/viewers";
import type { PanelId } from "../../lib/dockLayout";
import {
  dockPanelWindow,
  onPanelWindowState,
  sendPanelWindowAction,
  setPanelWindowPinned,
  setTitleBarHeight,
  type PanelWindowState,
} from "../../lib/panelWindows";
import { useTitleBarColors } from "../../lib/titleBar";
import { BackgroundTasksPanel } from "../BackgroundTasksPanel";
import { BrowserPanel } from "../BrowserPanel";
import { ViewerPanel } from "../ViewerPanel";
import { DockBackIcon, PinIcon } from "./windowIcons";
import { TaskPanel } from "../TaskPanel";
import { DockPanelContext, type DockPanelApi } from "./dockContext";

const TITLES: Record<PanelId, string> = {
  task: "Task details",
  browser: "Browser",
  background: "Background tasks",
  viewer: "Viewer",
};

const button =
  "flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]";
const noop = () => {};

/** One panel filling a window of its own. Its header is the window's title
 * bar; the main window sends what it shows and gets back what the user does. */
export function PanelWindow({ panel }: { panel: PanelId }) {
  const [state, setState] = useState<PanelWindowState | null>(null);
  const [pinned, setPinned] = useState(false);
  const [size, setSize] = useState(() => `${window.innerWidth}x${window.innerHeight}`);
  const controlsRef = useRef<HTMLDivElement>(null);
  useTitleBarColors();

  useEffect(() => onPanelWindowState(setState), []);
  useEffect(() => {
    const onResize = () => setSize(`${window.innerWidth}x${window.innerHeight}`);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const title = state?.panel === "viewer" && state.path ? (state.path.split(/[\\/]/).pop() ?? TITLES.viewer) : TITLES[panel];
  useEffect(() => {
    document.title = `${title} · coscribe`;
  }, [title]);

  // The OS buttons sit centred on the panel's own header row.
  const ready = state !== null;
  useLayoutEffect(() => {
    const box = controlsRef.current?.getBoundingClientRect();
    if (box) setTitleBarHeight(Math.round(2 * (box.top + box.height / 2)));
  }, [ready, panel]);

  const api: DockPanelApi = {
    id: panel,
    headerProps: { onPointerDown: noop, onDoubleClick: noop, "data-dock-header": true },
    controls: (
      <>
        <div ref={controlsRef} className="flex items-center gap-0.5">
          <button
            type="button"
            title="Return to the main window"
            aria-label="Return to the main window"
            className={button}
            onClick={dockPanelWindow}
          >
            <DockBackIcon className="h-4 w-4" strokeWidth={1.75} />
          </button>
          <button
            type="button"
            title={pinned ? "Stop keeping on top" : "Keep on top"}
            aria-label="Keep on top"
            aria-pressed={pinned}
            className={pinned ? `${button} bg-[var(--accent-soft)] !text-[var(--accent-ink)]` : button}
            onClick={() => void setPanelWindowPinned(!pinned).then(setPinned)}
          >
            <PinIcon className="h-4 w-4" strokeWidth={1.75} />
          </button>
        </div>
        <div className="titlebar-controls-space" />
      </>
    ),
    frozen: false,
    hidden: false,
    boundsKey: size,
  };

  return (
    <div className="panel-window flex h-screen flex-col overflow-hidden bg-[var(--panel-bg)] text-[var(--fg)]">
      <DockPanelContext.Provider value={api}>{state && <PanelContent state={state} />}</DockPanelContext.Provider>
    </div>
  );
}

function PanelContent({ state }: { state: PanelWindowState }) {
  if (state.panel === "task") {
    return (
      <TaskPanel
        threadId={state.threadId}
        title={state.title}
        task={state.task}
        run={state.run}
        workflowSteps={state.workflowSteps}
        refreshSignal={state.refreshSignal}
        busy={state.busy}
        onOpenTask={(task) => sendPanelWindowAction({ type: "openTask", task })}
        onSaveAsWorkflow={state.canSaveAsWorkflow ? () => sendPanelWindowAction({ type: "saveAsWorkflow" }) : undefined}
        onView={(path) => {
          if (!isViewable(path)) return false;
          sendPanelWindowAction({ type: "view", path });
          return true;
        }}
      />
    );
  }
  if (state.panel === "browser") {
    return (
      <BrowserPanel
        threadId={state.threadId}
        onSendToChat={(capture) => sendPanelWindowAction({ type: "sendToChat", capture })}
        onStopAgent={() => sendPanelWindowAction({ type: "stopAgent" })}
      />
    );
  }
  if (state.panel === "background") {
    return (
      <BackgroundTasksPanel
        key={state.threadId}
        threadId={state.threadId}
        refreshKey={state.refreshKey}
        focusTaskId={state.focusTaskId}
        onApprove={(id, approved, scope) => sendPanelWindowAction({ type: "approve", id, approved, scope })}
      />
    );
  }
  if (state.path) {
    return (
      <ViewerPanel
        key={`${state.threadId}:${state.path}`}
        threadId={state.threadId}
        path={state.path}
        refreshKey={state.refreshKey}
      />
    );
  }
  return <EmptyViewer />;
}

function EmptyViewer() {
  return (
    <ViewerHeader>
      <div className="flex flex-1 items-center justify-center px-6 text-center text-sm text-[var(--muted)]">
        No file is open in this conversation. Open a Word file or a presentation from Task details.
      </div>
    </ViewerHeader>
  );
}

function ViewerHeader({ children }: { children: React.ReactNode }) {
  return (
    <DockPanelContext.Consumer>
      {(dock) => (
        <div className="flex h-full min-h-0 flex-col">
          <div className="flex h-12 shrink-0 items-center gap-2 px-3" {...dock.headerProps}>
            <span className="min-w-0 flex-1 truncate px-1 text-[15px]">{TITLES.viewer}</span>
            {dock.controls}
          </div>
          {children}
        </div>
      )}
    </DockPanelContext.Consumer>
  );
}
