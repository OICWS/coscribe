import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { ChatLog } from "./components/ChatLog";
import { Composer, type ComposerSendPayload } from "./components/Composer";
import { ContextRing } from "./components/ContextRing";
import { ModePill } from "./components/ModePill";
import { ModelPicker } from "./components/ModelPicker";
import { BrowserPanel, type BrowserCapture } from "./components/BrowserPanel";
import { NAV_RAIL_EXPANDED_WIDTH, NavRail, type NavMode } from "./components/NavRail";
import type { PptxShapeCapture } from "./components/PptxShapeOverlay";
import { RunBreadcrumb } from "./components/RunBreadcrumb";
import { RunPanel } from "./components/RunPanel";
import { ScheduledTaskModal } from "./components/ScheduledTaskModal";
import { DirBrowserModal } from "./components/settings/DirBrowserModal";
import { SettingsModal } from "./components/settings/SettingsModal";
import { StartupSplash } from "./components/StartupSplash";
import { ShortcutsDialog } from "./components/ShortcutsDialog";
import { SubAgentsPanel } from "./components/SubAgentsPanel";
import { TaskPanel } from "./components/TaskPanel";
import { BrowserIcon, HelpIcon, PanelRightIcon, SettingsIcon, SubAgentsIcon } from "./components/icons";
import { ThreadHeader } from "./components/ThreadHeader";
import { getCommands, getScheduledTasks, getThreads, runScheduledTaskNow } from "./lib/rest";
import { goToThread, SCHEDULED_THREAD_PREFIX, startNewThread, THREAD_CHANGE_EVENT } from "./lib/nav";
import { latestRun, taskForThread } from "./lib/runLabels";
import { describeSchedule } from "./lib/scheduleLabels";
import { readStored, writeStored } from "./lib/storage";
import { connect, resolveThreadId, type AgentSocket, type ConnectionStatus } from "./lib/ws";
import { chatReducer, initialChatState, TASK_DRAFT_SAVED_PREFIX, type LogItem } from "./state/reducer";
import type { CommandInfo, ThreadSummary } from "./types/session";
import type { ScheduledTask } from "./types/settings";

type TaskDraftItem = Extract<LogItem, { kind: "task_draft" }>;

const TASK_PANEL_KEY = "coscribe.taskPanel.open";

function App() {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const socketRef = useRef<AgentSocket | null>(null);
  const [threadId, setThreadId] = useState(resolveThreadId);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [workspacePickerOpen, setWorkspacePickerOpen] = useState(false);
  const [browserPanelOpen, setBrowserPanelOpen] = useState(false);
  const [subAgentsPanelOpen, setSubAgentsPanelOpen] = useState(false);
  const [taskPanelWanted, setTaskPanelWanted] = useState(() => readStored(TASK_PANEL_KEY) !== "0");
  // Set by BrowserPanel's "Send to chat" (an element it picked, screenshot
  // + a short description) -- Composer watches this prop and appends it to
  // its own pendingImages the moment it changes, same "external image
  // arrives, composer adopts it" shape a drag-and-drop would use, just
  // driven by a prop instead of a DOM event. Cleared right back to null
  // after handing it off so the same capture can't be re-applied on an
  // unrelated future re-render.
  const [pendingBrowserCapture, setPendingBrowserCapture] = useState<BrowserCapture | null>(null);
  // Same pattern, for a shape clicked in a pptx preview (ChatLog.tsx's
  // PptxShapeOverlay) instead of an element picked in the Browser panel.
  const [pendingPptxCapture, setPendingPptxCapture] = useState<PptxShapeCapture | null>(null);
  // Same pattern again, for SkillsTab's "Create a skill" prefilling the
  // composer's own text (not an image) -- see onCreateSkill below.
  const [pendingComposerText, setPendingComposerText] = useState<string | null>(null);
  const [navMode, setNavMode] = useState<NavMode>("create");
  // Not persisted -- a per-page-load convenience, not a remembered
  // setting. Pinned, the rail takes layout space instead of overlaying
  // the header (see the column's paddingLeft below); hover-expanded it
  // still floats over the content.
  const [navPinned, setNavPinned] = useState(false);
  // Which task Scheduled mode shows the page for (null = the portal) --
  // an id, not a copy of the task, so it always reads the latest list.
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  // null = closed. { task: null } = create. { task } = editing that task.
  // `draftId` marks a create prefilled from a model's draft in chat,
  // whose outcome goes back to the model as the tool's answer.
  const [scheduledTaskModal, setScheduledTaskModal] = useState<{
    task: ScheduledTask | null;
    draft?: TaskDraftItem;
  } | null>(null);
  // One shared copy for the sidebar, portal, task page and run header --
  // REST mutations don't flow through the websocket, so every mutation
  // calls refreshScheduledTasks itself.
  const [scheduledTasks, setScheduledTasks] = useState<ScheduledTask[]>([]);
  const refreshScheduledTasks = useCallback(() => {
    getScheduledTasks()
      .then(setScheduledTasks)
      .catch(() => {});
  }, []);
  const [commands, setCommands] = useState<CommandInfo[]>([]);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("connecting");
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const refreshThreads = useCallback(() => {
    getThreads()
      .then(setThreads)
      // An empty list rather than null on failure, so a bad fetch can't
      // leave the startup splash below up forever.
      .catch(() => setThreads((prev) => prev ?? []));
  }, []);
  const renameThreadLocally = useCallback((id: string, title: string) => {
    setThreads((prev) => prev?.map((t) => (t.thread_id === id ? { ...t, preview: title } : t)) ?? prev);
  }, []);
  // Latches true once, so the startup splash below never comes back on a
  // later thread switch -- that case only swaps the chat area.
  const [bootstrapped, setBootstrapped] = useState(false);
  if (!bootstrapped && state.historyReceived && threads !== null) setBootstrapped(true);
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

  const threadIdRef = useRef(threadId);
  threadIdRef.current = threadId;
  useEffect(() => {
    const syncFromUrl = () => {
      const id = resolveThreadId();
      setNavMode("create");
      setSelectedTaskId(null);
      if (id === threadIdRef.current) return;
      pendingLocalSendsRef.current = [];
      dispatch({ type: "local_switch_thread" });
      setThreadId(id);
    };
    window.addEventListener(THREAD_CHANGE_EVENT, syncFromUrl);
    window.addEventListener("popstate", syncFromUrl);
    return () => {
      window.removeEventListener(THREAD_CHANGE_EVENT, syncFromUrl);
      window.removeEventListener("popstate", syncFromUrl);
    };
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
  }, []);

  // Refetched whenever a turn finishes (turnTick, same signal
  // RunPanel's refreshKey uses), not just on mount -- a brand-new thread
  // has no ThreadSummary/preview yet until its first turn completes, so
  // the header label below needs this to pick that up once it exists.
  useEffect(() => {
    refreshThreads();
  }, [state.turnTick, refreshThreads]);

  const sessionLabel = threads?.find((t) => t.thread_id === threadId)?.preview || "New session";

  // A scheduled run's own conversation (see tools/scheduled_tasks.py's
  // run_thread_id): gets the "Scheduled / <task>" breadcrumb instead of
  // ThreadHeader, and the sidebar stays on the task list.
  const isScheduledTaskThread = threadId.startsWith(SCHEDULED_THREAD_PREFIX);
  const threadTask = isScheduledTaskThread ? taskForThread(scheduledTasks, threadId) : null;
  const threadRun = threadTask?.runs.find((r) => r.thread_id === threadId) ?? null;
  // Browser and Sub Agents take the same right-hand space, so either one
  // open hides this panel without forgetting that it's wanted.
  const taskPanelShown =
    navMode === "create" &&
    taskPanelWanted &&
    !browserPanelOpen &&
    !subAgentsPanelOpen &&
    state.historyReceived &&
    (state.items.length > 0 || state.olderItems.length > 0);
  const finishedToolCalls = state.items.filter((item) => item.kind === "tool" && item.result !== undefined).length;
  const toggleTaskPanel = () => {
    const next = !taskPanelShown;
    setTaskPanelWanted(next);
    writeStored(TASK_PANEL_KEY, next ? "1" : "0");
    if (next) {
      setBrowserPanelOpen(false);
      setSubAgentsPanelOpen(false);
    }
  };
  const selectedTask = scheduledTasks.find((t) => t.trigger_id === selectedTaskId) ?? null;
  const showingScheduled = navMode === "run" || isScheduledTaskThread;

  useEffect(() => {
    refreshScheduledTasks();
  }, [state.turnTick, refreshScheduledTasks]);

  // Runs start and finish in the background (the poller, or Run now), so
  // while anything Scheduled is on screen the list is re-read on a timer
  // -- quickly while a run is in progress, slowly otherwise.
  const anyRunInProgress = scheduledTasks.some((t) => latestRun(t)?.status === "running");
  useEffect(() => {
    if (!showingScheduled) return;
    const interval = setInterval(refreshScheduledTasks, anyRunInProgress ? 3000 : 15000);
    return () => clearInterval(interval);
  }, [showingScheduled, anyRunInProgress, refreshScheduledTasks]);

  /** A sidebar row / portal card click: straight to the task's latest run
   * (its conversation), or to the task's page if it has never run. */
  const openScheduledTask = (task: ScheduledTask) => {
    const run = latestRun(task);
    if (run) {
      goToThread(run.thread_id);
      return;
    }
    setSelectedTaskId(task.trigger_id);
    setNavMode("run");
  };

  const showScheduledTaskPage = (task: ScheduledTask | null) => {
    setSelectedTaskId(task?.trigger_id ?? null);
    setNavMode("run");
  };

  const runTaskNow = async (task: ScheduledTask) => {
    const result = await runScheduledTaskNow(task.trigger_id);
    if ("error" in result) {
      dispatch({ type: "error", message: result.error });
      return;
    }
    refreshScheduledTasks();
    goToThread(result.run.thread_id);
  };

  /** "Create with coscribe": a fresh chat, pre-seeded so the model knows
   * the goal is a task -- it drafts one (create_scheduled_task) once it
   * has what it needs, and the draft comes back here for review. */
  const createTaskWithCoscribe = () => {
    startNewThread();
    setPendingComposerText("I'd like to set up a scheduled task: ");
  };

  const onReviewTaskDraft = (item: TaskDraftItem) => setScheduledTaskModal({ task: null, draft: item });

  const onDismissTaskDraft = (item: TaskDraftItem) => {
    dispatch({ type: "local_task_draft_resolved", id: item.id, status: "dismissed" });
    socketRef.current?.send({
      type: "question_response",
      id: item.id,
      answer: "The user dismissed the draft without saving it.",
    });
  };

  const onTaskSaved = (task: ScheduledTask) => {
    refreshScheduledTasks();
    const draft = scheduledTaskModal?.draft;
    if (!draft) return;
    dispatch({ type: "local_task_draft_resolved", id: draft.id, status: "saved", savedName: task.name });
    socketRef.current?.send({
      type: "question_response",
      id: draft.id,
      answer: `${TASK_DRAFT_SAVED_PREFIX} as scheduled task "${task.name}" (${describeSchedule(task.schedule)}). They may have edited it before saving.`,
    });
  };

  const onNavModeChange = (mode: NavMode) => {
    if (mode === "run") {
      showScheduledTaskPage(null);
      return;
    }
    // Leaving Scheduled from a run's conversation goes back to chatting,
    // not to that run's thread under a different sidebar.
    if (isScheduledTaskThread) {
      const recent = threads?.[0];
      if (recent) goToThread(recent.thread_id);
      else startNewThread();
      return;
    }
    setNavMode("create");
  };

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
      dispatch({
        type: "local_user_message",
        text: payload.displayText,
        instant: payload.isInstant,
        images: payload.images,
      });
      socketRef.current?.send({ type: "user_message", text: payload.outgoingText, images: payload.images });
    });
  };

  const onStop = () => socketRef.current?.send({ type: "stop" });

  const onEditMessage = (turnIndex: number, text: string) => {
    dispatch({ type: "local_edit_message", turnIndex, text });
    socketRef.current?.send({ type: "edit_message", index: turnIndex, text });
  };

  /** Undo, not regenerate -- truncates the turn like onEditMessage does,
   * but hands the original question text back to the composer
   * (pendingComposerText/Composer's externalText prop, same mechanism
   * onCreateSkill below uses) instead of resubmitting it. No turn runs
   * afterward, unlike edit. */
  const onRewindMessage = (turnIndex: number, text: string) => {
    dispatch({ type: "local_rewind_message", turnIndex });
    setPendingComposerText(text);
    socketRef.current?.send({ type: "rewind_message", index: turnIndex });
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

  // Guarded by olderStatus in the reducer (a second call while one's
  // already "loading" is a no-op there) -- ChatLog.tsx also only offers
  // this while status is "idle", but a fast double scroll-to-top could
  // still fire this twice before the first dispatch re-renders, so the
  // reducer's own guard is the real one, this is just the send-request
  // half of it.
  const onLoadOlderMessages = () => {
    dispatch({ type: "local_request_older_messages" });
    socketRef.current?.send({ type: "load_older_messages" });
  };

  const onSelectWorkspace = (path: string) => socketRef.current?.send({ type: "select_workspace", path });
  const onBrowserPanelCapture = (capture: BrowserCapture) => setPendingBrowserCapture(capture);
  const onPptxShapePicked = (capture: PptxShapeCapture) => setPendingPptxCapture(capture);

  const onToggleSkill = (name: string, enabled: boolean) => {
    const next = new Set(state.enabledSkills);
    if (enabled) next.add(name);
    else next.delete(name);
    socketRef.current?.send({ type: "select_skills", skills: [...next] });
  };

  /** Settings > Skills > Add > Create a skill -- closes Settings, switches
   * to Create mode (Composer only renders there), and prefills
   * "/skill-creator " into the composer via pendingComposerText -- not
   * sent, so the user can review or add context before hitting Enter. */
  const onCreateSkill = () => {
    setSettingsOpen(false);
    setNavMode("create");
    setPendingComposerText("/skill-creator ");
  };

  if (!bootstrapped) return <StartupSplash />;

  return (
    <div className="relative flex h-full">
      <div
        className="flex h-full min-w-0 flex-1 flex-col"
        style={navPinned ? { paddingLeft: NAV_RAIL_EXPANDED_WIDTH } : undefined}
      >
        <NavRail
          threadId={threadId}
          pinned={navPinned}
          onPinnedChange={setNavPinned}
          threads={threads ?? []}
          onThreadsChanged={refreshThreads}
          onThreadRenamed={renameThreadLocally}
          mode={showingScheduled ? "run" : "create"}
          onModeChange={onNavModeChange}
          scheduledTasks={scheduledTasks}
          onScheduledTasksChanged={refreshScheduledTasks}
          activeTaskId={navMode === "run" ? selectedTaskId : (threadTask?.trigger_id ?? null)}
          onOpenScheduledTask={openScheduledTask}
          onEditScheduledTask={(task) => setScheduledTaskModal({ task })}
          onRunScheduledTaskNow={runTaskNow}
          onNewScheduledTask={createTaskWithCoscribe}
        />
        {/* pl-12 lives here, not on the page-level wrapper above -- it only
         * needs to clear NavRail's own collapsed footprint (a 48px-square
         * toggle button pinned to the top-left corner, `absolute` so it
         * doesn't consume layout space on its own), and that footprint
         * never extends below this row. Reserving the same 48px on every
         * row below (ChatLog/Composer) had no such collision to avoid --
         * it just pushed their own mx-auto-centered content off-center
         * from the window's true center for no reason. */}
        <div className={`flex items-center gap-1 py-2.5 pr-4 ${navPinned ? "pl-4" : "pl-12"}`}>
          {/* Only in Chat mode -- a chat thread's own label/workspace
           * badge has no meaning while Run mode's Scheduled portal/
           * detail page is showing instead (a real, previously-confirmed
           * bug: this used to render unconditionally). A flex-1 spacer
           * keeps the icon buttons right-aligned either way, matching
           * ThreadHeader's own flex-1 when it is shown. */}
          {navMode === "create" && isScheduledTaskThread ? (
            <RunBreadcrumb
              task={threadTask}
              threadId={threadId}
              onOpenPortal={() => showScheduledTaskPage(null)}
              onOpenTask={showScheduledTaskPage}
            />
          ) : navMode === "create" ? (
            <ThreadHeader
              sessionLabel={sessionLabel}
              workspaceRoot={state.workspaceRoot || null}
              workspaceExplicit={state.workspaceExplicit}
              onPickWorkspace={() => setWorkspacePickerOpen(true)}
            />
          ) : (
            <div className="min-w-0 flex-1" />
          )}
          {navMode === "create" && (
            <button
              type="button"
              title={taskPanelShown ? "Hide task details" : "Show task details"}
              aria-pressed={taskPanelShown}
              className={`hidden h-8 w-8 shrink-0 items-center justify-center rounded-md hover:bg-[var(--card-bg)] hover:text-[var(--fg)] lg:flex ${taskPanelShown ? "bg-[var(--card-bg)] text-[var(--fg)]" : "text-[var(--muted)]"}`}
              onClick={toggleTaskPanel}
            >
              <PanelRightIcon className="h-[18px] w-[18px]" />
            </button>
          )}
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
            title="Sub Agents"
            className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md hover:bg-[var(--card-bg)] hover:text-[var(--fg)] ${subAgentsPanelOpen ? "bg-[var(--card-bg)] text-[var(--fg)]" : "text-[var(--muted)]"}`}
            onClick={() => setSubAgentsPanelOpen((v) => !v)}
          >
            <SubAgentsIcon className="h-[18px] w-[18px]" />
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
              onRewindMessage={state.turnInFlight ? undefined : onRewindMessage}
              loading={!state.historyReceived}
              onReviewTaskDraft={onReviewTaskDraft}
              onDismissTaskDraft={onDismissTaskDraft}
              onPptxShapePicked={onPptxShapePicked}
              olderItems={state.olderItems}
              olderStatus={state.olderStatus}
              onLoadOlder={onLoadOlderMessages}
            />
            {/* Real, live-reported bug: this used to be a bare full-width
             * div, a sibling of ChatLog/Composer rather than living inside
             * either one's own centered column -- so a turn-ending error
             * (e.g. hitting a token-usage limit) rendered as a raw red
             * line spanning edge-to-edge from the window's left border,
             * instead of aligning with the chat column like everything
             * else on this page. mx-auto/max-w-[760px]/px-4 here match
             * ChatLog.tsx's own wrapper and Composer's root exactly. */}
            {state.error && (
              <div className="mx-auto w-full max-w-[760px] px-4 py-1 text-sm text-red-500">{state.error}</div>
            )}
            <Composer
              turnInFlight={state.turnInFlight}
              totalTokens={state.totalTokens}
              commands={commands}
              modePill={<ModePill planMode={state.planMode} acceptEdits={state.acceptEdits} sendRaw={sendRaw} />}
              modelPicker={<ModelPicker currentModel={state.model} onSwitch={onSwitchModel} />}
              usageRing={
                <ContextRing
                  threadId={threadId}
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
              externalPptxCapture={pendingPptxCapture}
              onExternalPptxCaptureConsumed={() => setPendingPptxCapture(null)}
              externalText={pendingComposerText}
              onExternalTextConsumed={() => setPendingComposerText(null)}
            />
          </>
        ) : (
          <RunPanel
            tasks={scheduledTasks}
            onScheduledTasksChanged={refreshScheduledTasks}
            selectedTask={selectedTask}
            onSelectTask={showScheduledTaskPage}
            onOpenTask={openScheduledTask}
            onEditTask={(task) => setScheduledTaskModal({ task })}
            onRunNow={runTaskNow}
            onCreateWithCoscribe={createTaskWithCoscribe}
          />
        )}
        {scheduledTaskModal && (
          <ScheduledTaskModal
            task={scheduledTaskModal.task}
            draft={scheduledTaskModal.draft?.draft}
            onClose={() => setScheduledTaskModal(null)}
            onSaved={onTaskSaved}
          />
        )}
        <SettingsModal
          open={settingsOpen}
          enabledSkills={state.enabledSkills}
          onToggleSkill={onToggleSkill}
          onCreateSkill={onCreateSkill}
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
      {taskPanelShown && (
        <TaskPanel
          threadId={threadId}
          title={sessionLabel}
          task={threadTask}
          run={threadRun}
          refreshSignal={`${state.turnTick}:${finishedToolCalls}`}
          onOpenTask={showScheduledTaskPage}
        />
      )}
      {browserPanelOpen && (
        <BrowserPanel onClose={() => setBrowserPanelOpen(false)} onSendToChat={onBrowserPanelCapture} />
      )}
      {subAgentsPanelOpen && (
        <SubAgentsPanel threadId={threadId} onClose={() => setSubAgentsPanelOpen(false)} />
      )}
    </div>
  );
}

export default App;
