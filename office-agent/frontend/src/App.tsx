import { useEffect, useReducer, useRef, useState } from "react";
import { ChatLog } from "./components/ChatLog";
import { Composer, type ComposerSendPayload } from "./components/Composer";
import { ContextRing } from "./components/ContextRing";
import { ModePill } from "./components/ModePill";
import { ModelPicker } from "./components/ModelPicker";
import { BrowserPanel, type BrowserCapture } from "./components/BrowserPanel";
import { NavRail, type NavMode, type RunTab } from "./components/NavRail";
import { RunPanel } from "./components/RunPanel";
import { DirBrowserModal } from "./components/settings/DirBrowserModal";
import { SettingsModal } from "./components/settings/SettingsModal";
import { ShortcutsDialog } from "./components/ShortcutsDialog";
import { BrowserIcon, HelpIcon, SettingsIcon } from "./components/icons";
import { ThreadHeader } from "./components/ThreadHeader";
import { getCommands, getThreads, getWorkflowRuns } from "./lib/rest";
import { connect, resolveThreadId, type AgentSocket, type ConnectionStatus } from "./lib/ws";
import { chatReducer, initialChatState } from "./state/reducer";
import type { CommandInfo, ThreadSummary } from "./types/session";

function App() {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const socketRef = useRef<AgentSocket | null>(null);
  const threadId = useRef(resolveThreadId()).current;
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [workspacePickerOpen, setWorkspacePickerOpen] = useState(false);
  const [browserPanelOpen, setBrowserPanelOpen] = useState(false);
  // Set by BrowserPanel's "Send to chat" (an element it picked, screenshot
  // + a short description) -- Composer watches this prop and appends it to
  // its own pendingImages the moment it changes, same "external image
  // arrives, composer adopts it" shape a drag-and-drop would use, just
  // driven by a prop instead of a DOM event. Cleared right back to null
  // after handing it off so the same capture can't be re-applied on an
  // unrelated future re-render.
  const [pendingBrowserCapture, setPendingBrowserCapture] = useState<BrowserCapture | null>(null);
  const [navMode, setNavMode] = useState<NavMode>("create");
  const [runTab, setRunTab] = useState<RunTab>("workflows");
  const [commands, setCommands] = useState<CommandInfo[]>([]);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("connecting");
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  // Actions queued by runOrQueueSend below while state.historyReceived is
  // still false -- see its own comment, and historyReceived's in
  // reducer.ts, for why a locally-echoed user bubble can't be dispatched
  // before this connection's own "history" event has landed.
  const pendingLocalSendsRef = useRef<(() => void)[]>([]);

  // Global "?" to open the shortcuts help, same "ignore while typing"
  // guard ModelPicker's own number-key handler uses -- otherwise typing a
  // literal "?" into the composer or an editing textarea would pop the
  // dialog instead of inserting the character.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "?") return;
      const tag = (event.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      event.preventDefault();
      setShortcutsOpen(true);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    const handleStatus = (status: ConnectionStatus) => {
      setConnectionStatus(status);
      // A fresh (re)connect gets its own fresh "state"+"history" pair --
      // anything queued against the *previous* connection's already-seen
      // history is now stale and must wait for the new one instead.
      if (status !== "open") dispatch({ type: "local_connection_reset" });
    };
    const socket = connect(threadId, dispatch, handleStatus);
    socketRef.current = socket;
    return () => socket.close();
  }, [threadId]);

  // Flushes pendingLocalSendsRef the moment this connection's history is
  // in -- runs after the render that applied it, so each queued thunk's
  // own dispatch lands on top of the now-correct base state instead of
  // racing to get there first.
  useEffect(() => {
    if (!state.historyReceived || pendingLocalSendsRef.current.length === 0) return;
    const queued = pendingLocalSendsRef.current;
    pendingLocalSendsRef.current = [];
    for (const run of queued) run();
  }, [state.historyReceived]);

  /** Dispatches the optimistic local bubble and sends the real
   * user_message together, once it's actually safe to do so -- see
   * historyReceived's docstring in reducer.ts for the race this closes.
   * The underlying WS message would still eventually reach the server
   * even without this (lib/ws.ts's own send() queues independently), but
   * only this queue keeps the two in sync: without it, the local bubble
   * shows immediately and then gets wiped the moment "history" (still
   * reflecting pre-turn state) arrives a moment later. */
  const runOrQueueSend = (run: () => void) => {
    if (state.historyReceived) run();
    else pendingLocalSendsRef.current.push(run);
  };

  useEffect(() => {
    getCommands().then(setCommands);
    getWorkflowRuns().then((runs) => dispatch({ type: "local_hydrate_workflow_runs", runs }));
  }, []);

  // Refetched whenever a turn finishes (workflowEventTick, same signal
  // RunPanel's refreshKey uses), not just on mount -- a brand-new thread
  // has no ThreadSummary/preview yet until its first turn completes, so
  // the header label below needs this to pick that up once it exists.
  useEffect(() => {
    getThreads().then(setThreads);
  }, [state.workflowEventTick]);

  const sessionLabel = threads.find((t) => t.thread_id === threadId)?.preview || "New session";

  /** Sends a bare user_message with no chat-log bubble -- for commands the
   * UI issues on the user's behalf (the mode pill toggling /plan or
   * /accept-edits), mirroring app.js's sendRaw(). */
  const sendRaw = (text: string) => socketRef.current?.send({ type: "user_message", text });

  const onSend = (payload: ComposerSendPayload) => {
    if (payload.displayText.trim().toLowerCase() === "/stop") {
      // Reaches the currently-running turn directly, not queued behind it
      // like a normal message would -- see web/session.py's request_stop().
      // Not routed through runOrQueueSend: a turn has to already be
      // running for /stop to mean anything, which itself requires
      // history to have long since arrived.
      dispatch({ type: "local_user_message", text: payload.displayText, instant: true });
      socketRef.current?.send({ type: "stop" });
      return;
    }
    runOrQueueSend(() => {
      dispatch({ type: "local_user_message", text: payload.displayText, instant: payload.isInstant });
      socketRef.current?.send({ type: "user_message", text: payload.outgoingText, images: payload.images });
    });
  };

  const onStop = () => socketRef.current?.send({ type: "stop" });

  const onEditMessage = (turnIndex: number, text: string) => {
    dispatch({ type: "local_edit_message", turnIndex, text });
    socketRef.current?.send({ type: "edit_message", index: turnIndex, text });
  };

  const onLocalError = (message: string) => dispatch({ type: "error", message });

  const onApprove = (id: string, approved: boolean) => {
    dispatch({ type: "local_approval_resolved", id, approved });
    socketRef.current?.send({ type: "approval_response", id, approved });
  };

  const onAnswerQuestion = (id: string, answer: string) => {
    dispatch({ type: "local_question_answered", id, answer });
    socketRef.current?.send({ type: "question_response", id, answer });
  };

  const onSwitchModel = (model: string) => socketRef.current?.send({ type: "switch_model", model });

  const onSelectWorkspace = (path: string) => socketRef.current?.send({ type: "select_workspace", path });
  const onBrowserPanelCapture = (capture: BrowserCapture) => setPendingBrowserCapture(capture);

  const onToggleSkill = (name: string, enabled: boolean) => {
    const next = new Set(state.enabledSkills);
    if (enabled) next.add(name);
    else next.delete(name);
    socketRef.current?.send({ type: "select_skills", skills: [...next] });
  };

  const onRunWorkflow = (name: string) => {
    // Switch back to Create so the running turn's messages are actually
    // visible -- ChatLog only renders while navMode === "create".
    setNavMode("create");
    const text = `/runworkflow ${name}`;
    runOrQueueSend(() => {
      dispatch({ type: "local_user_message", text, instant: false });
      socketRef.current?.send({ type: "user_message", text });
    });
  };

  /** EmptyState's suggestion chips -- same shape as onSend/onRunWorkflow
   * above, just with no composer text to clear first (nothing was ever
   * typed). Routing this through runOrQueueSend is what actually matters
   * here: these cards are clickable the instant the page paints, well
   * before this connection's own history round trip can complete. */
  const onSuggestion = (prompt: string) => {
    runOrQueueSend(() => {
      dispatch({ type: "local_user_message", text: prompt, instant: false });
      socketRef.current?.send({ type: "user_message", text: prompt });
    });
  };

  return (
    <div className="relative flex h-full">
      <div className="flex h-full min-w-0 flex-1 flex-col">
        <NavRail
          threadId={threadId}
          mode={navMode}
          onModeChange={setNavMode}
          runTab={runTab}
          onRunTabChange={setRunTab}
          workflowRuns={state.workflowRuns}
          onRunWorkflow={onRunWorkflow}
          onStop={onStop}
        />
        {/* pl-12 lives here, not on the page-level wrapper above -- it only
         * needs to clear NavRail's own collapsed footprint (a 48px-square
         * toggle button pinned to the top-left corner, `absolute` so it
         * doesn't consume layout space on its own), and that footprint
         * never extends below this row. Reserving the same 48px on every
         * row below (ChatLog/Composer) had no such collision to avoid --
         * it just pushed their own mx-auto-centered content off-center
         * from the window's true center for no reason. */}
        <div className="flex items-center gap-1 py-2.5 pl-12 pr-4">
          <ThreadHeader
            sessionLabel={sessionLabel}
            workspaceRoot={state.workspaceRoot || null}
            workspaceExplicit={state.workspaceExplicit}
            onPickWorkspace={() => setWorkspacePickerOpen(true)}
          />
          <button
            type="button"
            title="Browser"
            className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md hover:bg-[var(--card-bg)] hover:text-[var(--fg)] ${browserPanelOpen ? "bg-[var(--card-bg)] text-[var(--fg)]" : "text-[var(--muted)]"}`}
            onClick={() => setBrowserPanelOpen((v) => !v)}
          >
            <BrowserIcon className="h-[18px] w-[18px]" />
          </button>
          <button
            type="button"
            title="Keyboard shortcuts (?)"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
            onClick={() => setShortcutsOpen(true)}
          >
            <HelpIcon className="h-[18px] w-[18px]" />
          </button>
          <button
            type="button"
            title="Settings"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
            onClick={() => setSettingsOpen(true)}
          >
            <SettingsIcon className="h-[18px] w-[18px]" />
          </button>
        </div>
        {connectionStatus === "reconnecting" && (
          <div className="bg-yellow-500/20 px-4 py-1 text-center text-sm text-yellow-700 dark:text-yellow-300">
            Connection lost -- reconnecting...
          </div>
        )}
        {navMode === "create" ? (
          <>
            <ChatLog
              items={state.items}
              onApprove={onApprove}
              onAnswerQuestion={onAnswerQuestion}
              onEditMessage={state.turnInFlight ? undefined : onEditMessage}
              onSuggestion={onSuggestion}
            />
            {state.error && <div className="px-4 py-1 text-sm text-red-500">{state.error}</div>}
            <Composer
              turnInFlight={state.turnInFlight}
              totalTokens={state.totalTokens}
              commands={commands}
              modePill={<ModePill planMode={state.planMode} acceptEdits={state.acceptEdits} sendRaw={sendRaw} />}
              modelPicker={<ModelPicker currentModel={state.model} onSwitch={onSwitchModel} />}
              usageRing={
                <ContextRing
                  totalTokens={state.totalTokens}
                  contextWindow={state.contextWindow}
                  cacheStats={state.cacheStats}
                />
              }
              onSend={onSend}
              onStop={onStop}
              onLocalError={onLocalError}
              externalImage={pendingBrowserCapture}
              onExternalImageConsumed={() => setPendingBrowserCapture(null)}
            />
          </>
        ) : (
          <RunPanel runTab={runTab} onRunWorkflow={onRunWorkflow} refreshKey={state.workflowEventTick} />
        )}
        <SettingsModal
          open={settingsOpen}
          workflowEventTick={state.workflowEventTick}
          enabledSkills={state.enabledSkills}
          onToggleSkill={onToggleSkill}
          onClose={() => setSettingsOpen(false)}
        />
        {shortcutsOpen && <ShortcutsDialog onClose={() => setShortcutsOpen(false)} />}
        {workspacePickerOpen && (
          <DirBrowserModal
            onClose={() => setWorkspacePickerOpen(false)}
            onSelect={(path) => {
              setWorkspacePickerOpen(false);
              onSelectWorkspace(path);
            }}
          />
        )}
      </div>
      {browserPanelOpen && (
        <BrowserPanel onClose={() => setBrowserPanelOpen(false)} onSendToChat={onBrowserPanelCapture} />
      )}
    </div>
  );
}

export default App;
