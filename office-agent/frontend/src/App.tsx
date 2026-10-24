import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { ThreadIdContext } from "./components/threadContext";
import { HomeGreeting } from "./components/EmptyState";
import { ChatLog } from "./components/ChatLog";
import { Composer, type ComposerSendPayload } from "./components/Composer";
import { ContextRing } from "./components/ContextRing";
import { EnvironmentPicker } from "./components/EnvironmentPicker";
import { ModePill } from "./components/ModePill";
import { isElectron, onBrowserAgent, onShowShortcuts } from "./lib/electron";
import {
  type ComposerAction,
  type MenuCommand,
  onMenuCommand,
  requestComposerAction,
  requestFindStep,
} from "./lib/menuCommands";
import { FindInPage } from "./components/FindInPage";
import { DRAWS_TITLE_BAR, useTitleBarColors } from "./lib/titleBar";
import type { PlanChoice, PlanItem } from "./components/PlanCard";
import { EffortPicker } from "./components/EffortPicker";
import type { Effort } from "./components/EffortPicker";
import { ModelPicker } from "./components/ModelPicker";
import { BrowserPanel, type BrowserCapture } from "./components/BrowserPanel";
import { NAV_RAIL_EXPANDED_WIDTH, NavRail, type NavMode } from "./components/NavRail";
import type { PptxShapeCapture } from "./components/PptxShapeOverlay";
import { RunBreadcrumb, TaskPageBreadcrumb } from "./components/RunBreadcrumb";
import { RunPanel } from "./components/RunPanel";
import { ScheduledTaskModal } from "./components/ScheduledTaskModal";
import { SettingsModal, type SettingsCategory } from "./components/settings/SettingsModal";
import { StartupSplash } from "./components/StartupSplash";
import { ShortcutsDialog } from "./components/ShortcutsDialog";
import { BackgroundTasksPanel } from "./components/BackgroundTasksPanel";
import { ViewerPanel } from "./components/ViewerPanel";
import { PlanPanel } from "./components/PlanPanel";
import { TaskPanel } from "./components/TaskPanel";
import { setPlanComments, usePlanComments, withPlanComments } from "./lib/planComments";
import { planPanelData } from "./lib/taskCard";
import { PanelDock } from "./components/dock/PanelDock";
import { PanelToggles } from "./components/dock/PanelToggles";
import { usePinnedItems } from "./lib/toolbarItems";
import { PANEL_IDS, PANEL_TITLES, type PanelId } from "./lib/dockLayout";
import { isViewable } from "./lib/viewers";
import { useCommentSender } from "./lib/commentReplies";
import { artifactActions } from "./lib/artifacts";
import { useArtifactStart } from "./lib/useArtifactStart";
import { ArtifactsPage } from "./components/artifacts/ArtifactsPage";
import { useSteerNotes } from "./lib/steerNotes";
import { DetachScript, OpenDesignSystem, SteerActionsContext, ViewFile } from "./components/chatlog/contexts";
import { canOpenPanelWindows, focusPanelWindow, usePanelWindows } from "./lib/panelWindows";
import { WorkflowDraftPage } from "./components/workflow/WorkflowDraftPage";
import { WorkflowRunView } from "./components/workflow/WorkflowRunView";
import { PageHeader, TitleBar } from "./components/AppHeader";
import { ThreadHeader } from "./components/ThreadHeader";
import {
  answerWorkflowStep,
  getCommands,
  getScheduledTasks,
  getThreads,
  investigateWorkflowRun,
  retryWorkflowRun,
  stopWorkflowRun,
  runScheduledTaskNow,
  patchScheduledTask,
  type WorkflowDraftResult,
} from "./lib/rest";
import { useRunningBackgroundTasks } from "./lib/useRunningBackgroundTasks";
import { shortLabel } from "./lib/threadStatus";
import {
  goToThread,
  readPage,
  SCHEDULED_THREAD_PREFIX,
  showChat,
  showScheduled,
  startNewThread,
  THREAD_CHANGE_EVENT,
} from "./lib/nav";
import { latestRun, taskForThread } from "./lib/runLabels";
import { describeSchedule } from "./lib/scheduleLabels";
import { useSessionSignals } from "./lib/sessionSignals";
import type { WorkflowDraftEntry } from "./lib/transcriptGrouping";
import { EMPTY_WORKFLOW } from "./lib/workflowEdit";
import { recordKey, workflowProgress, workflowRunLabel } from "./lib/workflowProgress";
import { connect, resolveThreadId, type AgentSocket, type ConnectionStatus } from "./lib/ws";
import { chatReducer, initialChatState, TASK_DRAFT_SAVED_PREFIX, type LogItem } from "./state/reducer";
import { QuestionPanel } from "./components/QuestionCard";
import type { CommandInfo, ThreadSummary } from "./types/session";
import type { ScheduledRun, ScheduledTask } from "./types/settings";
import type { StepRecord, Workflow } from "./types/workflow";

type TaskDraftItem = Extract<LogItem, { kind: "task_draft" }>;

type ConversationPanel = Exclude<PanelId, "task">;

function App() {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const socketRef = useRef<AgentSocket | null>(null);
  const [threadId, setThreadId] = useState(resolveThreadId);
  useSessionSignals(threadId, state.turnInFlight, state.items);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsCategory, setSettingsCategory] = useState<SettingsCategory | undefined>(undefined);
  // What Design systems opens on: its new-system form ("new"), one system (its id) or the list.
  const [settingsDesign, setSettingsDesign] = useState<string | null>(null);
  const showDesignSystems = (start: string | null) => { setSettingsCategory("design-systems"); setSettingsDesign(start); setSettingsOpen(true); };
  // Find in page: null while closed; bumped to refocus an open one.
  const [findKey, setFindKey] = useState<number | null>(null);
  const [pendingComposerAction, setPendingComposerAction] = useState<ComposerAction | null>(null);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  // Which panels are open is remembered per conversation: each one's
  // sub-agents and browsing are its own. Where they sit is PanelDock's.
  const [panelsByThread, setPanelsByThread] = useState<Record<string, ConversationPanel[]>>({});
  const openPanels = panelsByThread[threadId] ?? [];
  // The file each conversation's viewer shows.
  const [viewerFiles, setViewerFiles] = useState<Record<string, string>>({});
  const viewerFile = openPanels.includes("viewer") ? viewerFiles[threadId] : undefined;
  const setPanelOpen = (panel: ConversationPanel, open: boolean) =>
    setPanelsByThread((panels) => {
      const current = threadIdRef.current;
      const list = (panels[current] ?? []).filter((p) => p !== panel);
      return { ...panels, [current]: open ? [...list, panel] : list };
    });
  const togglePanel = (panel: ConversationPanel) =>
    setPanelsByThread((panels) => {
      const current = threadIdRef.current;
      const list = panels[current] ?? [];
      return { ...panels, [current]: list.includes(panel) ? list.filter((p) => p !== panel) : [...list, panel] };
    });
  const [subAgentsTick, setSubAgentsTick] = useState(0);
  const [subAgentFocus, setSubAgentFocus] = useState<{ threadId: string; taskId: string } | null>(null);
  // Per conversation, this page load: whether the panel was opened or
  // closed by hand. Unset, it opens only when it has something to show.
  const [taskPanelChoice, setTaskPanelChoice] = useState<Record<string, boolean>>({});
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
  const artifactStart = useArtifactStart(threadId);
  const [navMode, setNavMode] = useState<NavMode>(() => (readPage().view === "scheduled" ? "run" : "create"));
  const [artifactsOpen, setArtifactsOpen] = useState(() => readPage().view === "artifacts");
  // Not persisted -- a per-page-load convenience, not a remembered
  // setting. Pinned, the rail takes layout space instead of overlaying
  // the header (see the column's paddingLeft below); hover-expanded it
  // still floats over the content.
  const [navPinned, setNavPinned] = useState(false);
  const [pinnedItems, setPinnedItems] = usePinnedItems();
  // Which task Scheduled mode shows the page for (null = the portal) --
  // an id, not a copy of the task, so it always reads the latest list.
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(() => readPage().task);
  // A workflow step to open on the task page -- set by a run's "Edit step".
  const [focusStepId, setFocusStepId] = useState<string | null>(null);
  // null = closed. { task: null } = create. { task } = editing that task.
  // `draftId` marks a create prefilled from a model's draft in chat,
  // whose outcome goes back to the model as the tool's answer.
  const [scheduledTaskModal, setScheduledTaskModal] = useState<{
    task: ScheduledTask | null;
    draft?: TaskDraftItem;
    newWorkflow?: Workflow;
    initialName?: string;
    initialEnvironment?: string | null;
    fromDraft?: string;
    fromThread?: string;
  } | null>(null);
  // A workflow being drafted from a conversation, shown in place of the
  // Scheduled page until it's saved, discarded or navigated away from.
  const [workflowDraft, setWorkflowDraft] = useState<{
    threadId: string;
    threadTitle: string;
    nameHint: string;
    initial?: WorkflowDraftResult;
    environment: string | null;
    /** Proposed changes to this saved task's workflow. */
    revision?: { triggerId: string; changes: string[] };
    /** The conversation draft under review. */
    draftId?: string;
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

  useEffect(() => {
    if (isElectron()) onShowShortcuts(() => setShortcutsOpen(true));
  }, []);

  // The desktop menu's File/Edit items; Ctrl+F does Find in a plain
  // browser too, where there's no menu to carry the shortcut.
  const menuHandler = useRef<(command: MenuCommand) => void>(() => {});
  // Off a conversation, Ctrl+F is left to the browser's own find.
  const findAvailable = useRef(false);
  useEffect(() => onMenuCommand((command) => menuHandler.current(command)), []);
  useEffect(() => {
    if (isElectron()) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!findAvailable.current) return;
      if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === "f") {
        event.preventDefault();
        menuHandler.current("find");
      } else if (event.key === "F3") {
        event.preventDefault();
        menuHandler.current(event.shiftKey ? "find-previous" : "find-next");
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  // coscribe's AI started using the browser: show it, so the user watches
  // each step happen.
  useEffect(
    () =>
      onBrowserAgent((payload) => {
        if (!payload.open) return;
        setPanelOpen("browser", true);
      }),
    [],
  );

  useTitleBarColors();

  const threadIdRef = useRef(threadId);
  threadIdRef.current = threadId;
  useEffect(() => {
    const syncFromUrl = () => {
      const id = resolveThreadId();
      const page = readPage();
      setNavMode(page.view === "scheduled" ? "run" : "create");
      setArtifactsOpen(page.view === "artifacts");
      setSelectedTaskId(page.task);
      setWorkflowDraft(null);
      setFocusStepId(null);
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
    const socket = connect(
      threadId,
      (event) => {
        if (event.type === "thread_titled") {
          renameThreadLocally(threadId, event.title);
          return;
        }
        if (event.type === "subagents_changed" || event.type === "background_tasks_changed") {
          setSubAgentsTick((tick) => tick + 1);
          return;
        }
        if (event.type === "approval_required" && event.subagent_id) {
          // Its approval card lives in the Background tasks panel.
          setSubAgentFocus({ threadId, taskId: event.subagent_id });
          setPanelOpen("background", true);
          setSubAgentsTick((tick) => tick + 1);
          return;
        }
        if (event.type !== "workflow_step") {
          dispatch(event);
          return;
        }
        // Step records live on the task list's run; patch it in place so
        // the run view follows each step without waiting for a poll.
        setScheduledTasks((tasks) =>
          tasks.map((task) => ({
            ...task,
            runs: task.runs.map((run) => {
              if (run.run_id !== event.run_id) return run;
              const key = recordKey(event.record.step_id, event.record.iteration);
              const same = (step: StepRecord) => recordKey(step.step_id, step.iteration) === key;
              const steps = run.steps.some(same)
                ? run.steps.map((step) => (same(step) ? event.record : step))
                : [...run.steps, event.record];
              return { ...run, steps };
            }),
          })),
        );
      },
      handleStatus,
    );
    socketRef.current = socket;
    return () => socket.close();
  }, [threadId, renameThreadLocally]);

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

  const current = threads?.find((t) => t.thread_id === threadId);
  const sessionLabel = (current && shortLabel(current.preview)) || "New session";

  // A scheduled run's own conversation (see tools/bookkeeping/scheduled_tasks.py's
  // run_thread_id): gets the "Scheduled / <task>" breadcrumb instead of
  // ThreadHeader, and the sidebar stays on the task list.
  const isScheduledTaskThread = threadId.startsWith(SCHEDULED_THREAD_PREFIX);
  const threadTask = isScheduledTaskThread ? taskForThread(scheduledTasks, threadId) : null;
  const threadRun = threadTask?.runs.find((r) => r.thread_id === threadId) ?? null;
  const threadWorkflow = threadRun ? (threadTask?.workflow ?? null) : null;
  const planData = planPanelData(state.items);
  const hasPlan = planData.tasks.length > 0;
  const planComments = usePlanComments(planData.plan);
  const taskPanelWanted = taskPanelChoice[threadId] ?? (threadWorkflow !== null || isScheduledTaskThread || hasPlan);
  const onHome =
    state.historyReceived &&
    state.items.length === 0 &&
    state.olderItems.length === 0 &&
    !state.turnInFlight &&
    !isScheduledTaskThread &&
    threadWorkflow === null;
  const taskPanelShown =
    navMode === "create" &&
    taskPanelWanted &&
    (threadWorkflow !== null || (state.historyReceived && (state.items.length > 0 || state.olderItems.length > 0)));
  const finishedToolCalls = state.items.filter((item) => item.kind === "tool" && item.result !== undefined).length;
  const toggleTaskPanel = () => {
    if (poppedOut.includes("task")) {
      focusPanelWindow("task");
      return;
    }
    const next = !taskPanelShown;
    setTaskPanelChoice((choices) => ({ ...choices, [threadId]: next }));
  };
  const selectedTask = scheduledTasks.find((t) => t.trigger_id === selectedTaskId) ?? null;
  // Browser and Background tasks belong to a conversation, not the Scheduled pages.
  const inConversation = navMode === "create" && !onHome && !artifactsOpen;
  const subAgents = useRunningBackgroundTasks(threadId, subAgentsTick, inConversation);
  let subAgentsTitle = "Background tasks";
  if (subAgents.running > 0) {
    subAgentsTitle += ` (${subAgents.running} running`;
    subAgentsTitle += subAgents.waiting > 0 ? `, ${subAgents.waiting} waiting for you)` : ")";
  }

  findAvailable.current = inConversation;
  menuHandler.current = (command) => {
    if (command === "new-session" || command === "close-session") startNewThread();
    else if (command === "settings") setSettingsOpen(true);
    else if (command === "find") {
      if (inConversation) setFindKey((key) => (key ?? 0) + 1);
    } else if (command === "find-next" || command === "find-previous") {
      if (!inConversation) return;
      if (findKey === null) setFindKey(1);
      else requestFindStep(command === "find-next" ? 1 : -1);
    } else if (navMode === "create") requestComposerAction(command);
    else {
      // From a Scheduled page: back to the conversation, whose composer
      // then takes the action.
      setPendingComposerAction(command);
      showChat();
    }
  };

  useEffect(() => {
    if (!pendingComposerAction || navMode !== "create") return;
    requestComposerAction(pendingComposerAction);
    setPendingComposerAction(null);
  }, [pendingComposerAction, navMode]);

  useEffect(() => setFindKey(null), [threadId]);
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
    showScheduled(task.trigger_id);
  };

  // A task made from this conversation works in the conversation's folder.
  const threadEnvironment = state.environment;

  const startWorkflowDraft = (nameHint: string) => {
    setWorkflowDraft({ threadId, threadTitle: sessionLabel, nameHint, environment: threadEnvironment });
    setScheduledTaskModal(null);
    setNavMode("run");
  };

  const reviewWorkflowDraft = (entry: WorkflowDraftEntry) => {
    setWorkflowDraft({
      threadId,
      threadTitle: sessionLabel,
      nameHint: entry.name,
      initial: { name: entry.name, workflow: entry.workflow, notes: entry.notes },
      environment: entry.environment ?? threadEnvironment,
      revision: entry.triggerId ? { triggerId: entry.triggerId, changes: entry.changes } : undefined,
      draftId: entry.draftId ?? undefined,
    });
    setScheduledTaskModal(null);
    setNavMode("run");
  };

  const savedDrafts = new Map<string, { triggerId: string; name: string }>();
  for (const task of scheduledTasks) {
    for (const draftId of task.drafts ?? []) savedDrafts.set(draftId, { triggerId: task.trigger_id, name: task.name });
  }
  const draftCards = {
    saved: savedDrafts,
    testProgress: state.workflowTest,
    onTestAgain: state.turnInFlight
      ? undefined
      : (entry: WorkflowDraftEntry) => sendRaw(`Test the workflow draft ${entry.draftId} again.`),
    onContinue: () => document.querySelector<HTMLTextAreaElement>('[data-testid="composer"] textarea')?.focus(),
    onOpenTask: (triggerId: string) => {
      const task = scheduledTasks.find((t) => t.trigger_id === triggerId);
      if (task) showScheduledTaskPage(task);
    },
  };

  const leaveWorkflowDraft = () => {
    const draftThread = workflowDraft?.threadId;
    if (draftThread && draftThread !== threadId) goToThread(draftThread);
    else showChat();
  };

  const showScheduledTaskPage = (task: ScheduledTask | null) => showScheduled(task?.trigger_id ?? null);

  const editWorkflowStep = (task: ScheduledTask, stepId: string) => {
    showScheduledTaskPage(task);
    setFocusStepId(stepId);
  };

  const retryWorkflow = async (task: ScheduledTask, run: ScheduledRun, stepId?: string) => {
    const error = await retryWorkflowRun(task.trigger_id, run.run_id, stepId);
    refreshScheduledTasks();
    return error;
  };

  const answerWorkflow = async (task: ScheduledTask, run: ScheduledRun, approved: boolean, note: string) => {
    const error = await answerWorkflowStep(task.trigger_id, run.run_id, approved, note);
    refreshScheduledTasks();
    return error;
  };

  const stopWorkflow = async (task: ScheduledTask, run: ScheduledRun) => {
    const error = await stopWorkflowRun(task.trigger_id, run.run_id);
    refreshScheduledTasks();
    return error;
  };

  const investigateWorkflow = async (task: ScheduledTask, run: ScheduledRun, model: string | null) => {
    const result = await investigateWorkflowRun(task.trigger_id, run.run_id, model);
    if ("error" in result) return result.error;
    goToThread(result.thread_id);
    return null;
  };

  const runTaskNow = async (task: ScheduledTask, inputs?: Record<string, unknown>) => {
    const result = await runScheduledTaskNow(task.trigger_id, inputs);
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

  const onReviewTaskDraft = (item: TaskDraftItem) => {
    const existing = item.draft.trigger_id
      ? (scheduledTasks.find((task) => task.trigger_id === item.draft.trigger_id) ?? null)
      : null;
    setScheduledTaskModal({ task: existing, draft: item, initialEnvironment: threadEnvironment });
  };

  const saveWorkflowRevision = async (triggerId: string, workflow: Workflow) => {
    const task = scheduledTasks.find((t) => t.trigger_id === triggerId);
    if (!task) return "That task no longer exists.";
    const fromDraft = workflowDraft?.draftId;
    const result = await patchScheduledTask(triggerId, { workflow, ...(fromDraft ? { from_draft: fromDraft } : {}) });
    if ("error" in result) return result.error;
    refreshScheduledTasks();
    showScheduledTaskPage(result);
    return null;
  };

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
    if (scheduledTaskModal?.newWorkflow) showScheduledTaskPage(task);
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
    showChat();
  };

  /** Sends a bare user_message with no chat-log bubble -- for commands the
   * UI issues on the user's behalf (the mode pill toggling /plan or
   * /accept-edits), mirroring app.js's sendRaw(). */
  const sendRaw = (text: string) => socketRef.current?.send({ type: "user_message", text });

  const onSend = (payload: ComposerSendPayload) => {
    const saveWorkflow = /^\/saveworkflow(?:\s+([\s\S]*))?$/i.exec(payload.displayText.trim());
    if (saveWorkflow) {
      startWorkflowDraft((saveWorkflow[1] ?? "").trim());
      return;
    }
    if (payload.displayText.trim().toLowerCase() === "/stop") {
      // Reaches the currently-running turn directly, not queued behind it
      // like a normal message would -- see conversation/session.py's request_stop().
      // Not routed through runOrQueueSend: a turn has to already be
      // running for /stop to mean anything, which itself requires
      // history to have long since arrived.
      dispatch({ type: "local_user_message", text: payload.displayText, instant: true });
      onStop();
      return;
    }
    runOrQueueSend(() => {
      dispatch({
        type: "local_user_message",
        text: payload.displayText,
        instant: payload.isInstant,
        images: payload.images,
        files: payload.files,
      });
      socketRef.current?.send({ type: "user_message", text: payload.outgoingText, images: payload.images, ...artifactStart.take(payload.displayText) });
    });
  };

  const onSendComment = useCommentSender(threadId, state.items, state.turnInFlight, (text) => onSend({ displayText: text, outgoingText: text, isInstant: false }));

  const steers = useSteerNotes(state, dispatch, (message) => socketRef.current?.send(message), setPendingComposerText, onSend);
  const { steer: onSteer, stop: onStop } = steers;

  const onEditMessage = (turnIndex: number, text: string) => {
    dispatch({ type: "local_edit_message", turnIndex, text });
    socketRef.current?.send({ type: "edit_message", index: turnIndex, text });
  };

  /** Undo, not regenerate: the question goes back to the composer instead of being sent again,
   * so no turn runs, unlike edit. */
  const onRewindMessage = (turnIndex: number, text: string) => {
    dispatch({ type: "local_rewind_message", turnIndex });
    setPendingComposerText(text);
    socketRef.current?.send({ type: "rewind_message", index: turnIndex });
  };

  const onLocalError = (message: string) => dispatch({ type: "error", message });

  const onApprove = (id: string, approved: boolean, scope?: "task") => {
    dispatch({ type: "local_approval_resolved", id, approved });
    socketRef.current?.send({ type: "approval_response", id, approved, ...(scope ? { scope } : {}) });
  };

  const onDetachScript = (script: string) => socketRef.current?.send({ type: "detach_command", script });

  const onAnswerPlan = (item: PlanItem, choice: PlanChoice, feedback: string) => {
    dispatch({ type: "local_plan_answered", id: item.id, status: choice });
    socketRef.current?.send({
      type: "question_response",
      id: item.id,
      answer: choice === "revise" ? `revise:${withPlanComments(item.plan, feedback)}` : choice,
    });
  };

  const onAnswerQuestion = (id: string, answers: (string | null)[] | null) => {
    dispatch({ type: "local_question_answered", id, answers });
    socketRef.current?.send(
      answers ? { type: "question_response", id, answers } : { type: "question_response", id, dismissed: true },
    );
  };
  const pendingQuestion = state.items.findLast(
    (item): item is Extract<LogItem, { kind: "question" }> => item.kind === "question" && item.status === "pending",
  );

  const onSwitchModel = (model: string) => socketRef.current?.send({ type: "switch_model", model });
  const onSetEffort = (effort: Effort) =>
    socketRef.current?.send({ type: "switch_model", model: state.model, effort });

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

  const onSetEnvironment = (environment: string) => socketRef.current?.send({ type: "set_environment", environment });
  const onBrowserPanelCapture = (capture: BrowserCapture) => setPendingBrowserCapture(capture);
  const onPptxShapePicked = (capture: PptxShapeCapture) => setPendingPptxCapture(capture);

  /** Create mode, because the composer only renders there; the command is prefilled, not sent,
   * so the user can add context before sending. */
  const onCreateSkill = () => {
    setSettingsOpen(false);
    showChat();
    setPendingComposerText("/skill-creator ");
  };

  const closePanel = (panel: PanelId) => {
    if (panel === "task") {
      setTaskPanelChoice((choices) => ({ ...choices, [threadId]: false }));
      return;
    }
    setPanelOpen(panel, false);
    if (panel === "background") setSubAgentFocus(null);
  };
  const refreshKey = `${state.turnTick}:${finishedToolCalls}`;
  const workflowSteps = threadWorkflow && threadRun ? workflowProgress(threadWorkflow, threadRun) : null;
  const refreshSignal = `${refreshKey}:${threadRun?.steps.length ?? 0}:${threadRun?.status ?? ""}`;
  const taskProps = { threadId, title: sessionLabel, task: threadTask, run: threadRun, workflowSteps, refreshSignal };
  const focusTaskId = subAgentFocus?.threadId === threadId ? subAgentFocus.taskId : null;
  const backgroundProps = { threadId, refreshKey: subAgentsTick, focusTaskId };
  const { poppedOut, popOut } = usePanelWindows({
    states: {
      task: { panel: "task", ...taskProps, busy: state.turnInFlight, canSaveAsWorkflow: !threadTask },
      browser: { panel: "browser", threadId },
      background: { panel: "background", ...backgroundProps },
      viewer: { panel: "viewer", threadId, path: viewerFiles[threadId] ?? null, refreshKey },
      plan: { panel: "plan", threadId, ...planData, comments: planComments, busy: state.turnInFlight },
    },
    titles: PANEL_TITLES,
    threadId,
    onAction: (action) => {
      if (action.type === "openTask") showScheduledTaskPage(action.task);
      else if (action.type === "saveAsWorkflow") startWorkflowDraft("");
      else if (action.type === "view") viewFile(action.path);
      else if (action.type === "sendToChat") onBrowserPanelCapture(action.capture);
      else if (action.type === "sendComment") onSendComment(action.text, action.link);
      else if (action.type === "planComments") setPlanComments(action.plan, action.comments);
      else if (action.type === "stopAgent") onStop();
      else onApprove(action.id, action.approved, action.scope);
    },
    onClosed: (panel, dock) => {
      if (!dock) closePanel(panel);
    },
  });
  const artifacts = artifactActions((id, path) => {
    setViewerFiles((files) => ({ ...files, [id]: path }));
    setPanelOpen("viewer", true);
  }, artifactStart.start, (id) => showDesignSystems(id ?? "new"));
  const viewFile = (path: string) => {
    if (!isViewable(path)) return false;
    setViewerFiles((files) => ({ ...files, [threadIdRef.current]: path }));
    setPanelOpen("viewer", true);
    if (poppedOut.includes("viewer")) focusPanelWindow("viewer");
    return true;
  };

  if (!bootstrapped) return <StartupSplash />;

  const composer = (
    <Composer
      turnInFlight={state.turnInFlight}
      suggestion={state.suggestion}
      onSuggestionDismiss={() => dispatch({ type: "local_suggestion_dismissed" })}
      totalTokens={state.totalTokens}
      workflowTest={state.workflowTest}
      commands={commands}
      modePill={
        onHome ? null : (
          <ModePill
            planMode={state.planMode}
            acceptEdits={state.acceptEdits}
            autoMode={state.autoMode}
            sendRaw={sendRaw}
          />
        )
      }
      environmentPicker={
        <EnvironmentPicker current={state.environment} disabled={state.turnInFlight} onPick={onSetEnvironment} />
      }
      modelPicker={<ModelPicker currentModel={state.model} onSwitch={onSwitchModel} />}
      effortPicker={<EffortPicker effort={state.effort} runsAs={state.effortRunsAs} onChange={onSetEffort} />}
      usageRing={
        onHome ? null : (
          <ContextRing
            threadId={threadId}
            totalTokens={state.totalTokens}
            contextWindow={state.contextWindow}
            cacheStats={state.cacheStats}
          />
        )
      }
      onSend={onSend}
      onSteer={onSteer}
      onStop={onStop}
      onLocalError={onLocalError}
      externalImage={pendingBrowserCapture}
      onExternalImageConsumed={() => setPendingBrowserCapture(null)}
      externalPptxCapture={pendingPptxCapture}
      onExternalPptxCaptureConsumed={() => setPendingPptxCapture(null)}
      artifact={artifactStart.kind}
      onArtifactRemoved={artifactStart.clear}
      designSystem={artifactStart.designSystem}
      onDesignSystem={artifactStart.chooseDesignSystem}
      onManageDesignSystems={(startNew) => showDesignSystems(startNew ? "new" : null)}
      externalText={pendingComposerText}
      onExternalTextConsumed={() => setPendingComposerText(null)}
    />
  );

  const panelOpen = (panel: PanelId) =>
    panel === "task" ? taskPanelShown : panel === "viewer" ? Boolean(viewerFile) : openPanels.includes(panel);
  const visiblePanels = PANEL_IDS.filter((panel) => panelOpen(panel) && !poppedOut.includes(panel));
  const renderPanel = (panel: PanelId) => {
    if (panel === "task") {
      return (
        <TaskPanel
          {...taskProps}
          onOpenTask={showScheduledTaskPage}
          onSaveAsWorkflow={threadTask ? undefined : () => startWorkflowDraft("")}
          busy={state.turnInFlight}
          onView={viewFile}
          onOpenPlan={() => setPanelOpen("plan", true)}
        />
      );
    }
    if (panel === "viewer" && viewerFile) {
      return (
        <ViewerPanel key={`${threadId}:${viewerFile}`} threadId={threadId} path={viewerFile} refreshKey={refreshKey} busy={state.turnInFlight} onSendComment={onSendComment} onFileChanged={(changed) => (changed ? viewFile(changed) : closePanel("viewer"))} />
      );
    }
    if (panel === "browser") {
      return <BrowserPanel threadId={threadId} onSendToChat={onBrowserPanelCapture} onStopAgent={onStop} />;
    }
    if (panel === "plan") return <PlanPanel {...planData} comments={planComments} onComments={(c) => setPlanComments(planData.plan ?? "", c)} busy={state.turnInFlight} />;
    return (
      <BackgroundTasksPanel key={threadId} {...backgroundProps} onApprove={onApprove} />
    );
  };

  // The page's title: the conversation's name, or where in Scheduled this is.
  const pageTitle =
    artifactsOpen ? (
      <div className="min-w-0 flex-1" />
    ) : navMode === "create" && isScheduledTaskThread ? (
      <RunBreadcrumb
        task={threadTask}
        threadId={threadId}
        statusLabel={threadWorkflow && threadRun ? workflowRunLabel(threadWorkflow, threadRun) : null}
        onOpenPortal={() => showScheduledTaskPage(null)}
        onOpenTask={showScheduledTaskPage}
      />
    ) : navMode === "create" && !onHome ? (
      <ThreadHeader sessionLabel={sessionLabel} />
    ) : selectedTask && !workflowDraft ? (
      <TaskPageBreadcrumb task={selectedTask} onOpenPortal={() => showScheduledTaskPage(null)} />
    ) : (
      <div className="min-w-0 flex-1" />
    );

  const togglePanelOrWindow = (panel: Exclude<PanelId, "task">) => {
    if (poppedOut.includes(panel)) focusPanelWindow(panel);
    else togglePanel(panel);
  };
  const panelToggles = (
    <PanelToggles
      open={(panel) => panelOpen(panel) || poppedOut.includes(panel)}
      onToggle={(panel) => (panel === "task" ? toggleTaskPanel() : togglePanelOrWindow(panel))}
      inConversation={inConversation}
      pinned={pinnedItems}
      onPinnedChange={setPinnedItems}
      sidebar={{ open: navPinned, toggle: () => setNavPinned(!navPinned) }}
      artifactsOpen={artifactsOpen}
      backgroundTitle={subAgentsTitle}
      running={subAgents.running}
      waiting={subAgents.waiting}
    />
  );

  return (
    <ThreadIdContext.Provider value={threadId}>
    <div className="relative flex h-full flex-col">
      {DRAWS_TITLE_BAR && <TitleBar navPinned={navPinned} title={pageTitle} toggles={panelToggles} onSettings={() => setSettingsOpen(true)} />}
      <div className="relative flex min-h-0 flex-1">
        <PanelDock
          visible={inConversation ? visiblePanels : []}
          titles={PANEL_TITLES}
          render={renderPanel}
          onClose={closePanel}
          onPopOut={canOpenPanelWindows() ? popOut : undefined}
          leftInset={navPinned ? NAV_RAIL_EXPANDED_WIDTH : 0}
          chat={
            <div className="relative flex h-full min-w-0 flex-1 flex-col">
              {inConversation && findKey !== null && <FindInPage focusKey={findKey} onClose={() => setFindKey(null)} />}
              {!DRAWS_TITLE_BAR && (
                <PageHeader navPinned={navPinned} title={pageTitle} toggles={panelToggles} onSettings={() => setSettingsOpen(true)} onShortcuts={() => setShortcutsOpen(true)} />
              )}
              {connectionStatus === "reconnecting" && (
                <div className="bg-yellow-500/20 px-4 py-1 text-center text-sm text-yellow-700 dark:text-yellow-300">
                  Connection lost -- reconnecting...
                </div>
              )}
              {artifactsOpen ? (
                <ArtifactsPage onOpen={artifacts.open} onMake={artifacts.make} />
              ) : navMode === "create" && threadWorkflow && threadTask && threadRun ? (
                <WorkflowRunView
                  task={threadTask}
                  run={threadRun}
                  workflow={threadWorkflow}
                  onRetry={(stepId) => retryWorkflow(threadTask, threadRun, stepId)}
                  onAnswer={(approved, note) => answerWorkflow(threadTask, threadRun, approved, note)}
                  onEditStep={(stepId) => editWorkflowStep(threadTask, stepId)}
                  onStop={() => stopWorkflow(threadTask, threadRun)}
                  onInvestigate={(model) => investigateWorkflow(threadTask, threadRun, model)}
                />
              ) : navMode === "create" && onHome ? (
                // A new conversation: greeting and message box centered in the page.
                <div className="flex min-h-0 flex-1 flex-col items-center justify-center pb-[6vh]">
                  <HomeGreeting />
                  {state.error && (
                    <div className="mx-auto w-full max-w-[880px] px-4 py-1 text-sm text-red-500">{state.error}</div>
                  )}
                  {composer}
                </div>
              ) : navMode === "create" ? (
                <SteerActionsContext.Provider value={steers.actions}><ViewFile.Provider value={viewFile}><OpenDesignSystem.Provider value={showDesignSystems}><DetachScript.Provider value={onDetachScript}>
                  <ChatLog
                    items={state.items}
                    turnInFlight={state.turnInFlight}
                    connectorTools={state.connectorTools}
                    onApprove={onApprove}
                    onAnswerPlan={onAnswerPlan}
                    onOpenPlan={() => setPanelOpen("plan", true)}
                    onEditMessage={state.turnInFlight ? undefined : onEditMessage}
                    onRewindMessage={state.turnInFlight ? undefined : onRewindMessage}
                    loading={!state.historyReceived}
                    onReviewTaskDraft={onReviewTaskDraft}
                    onDismissTaskDraft={onDismissTaskDraft}
                    onReviewWorkflowDraft={reviewWorkflowDraft}
                    draftCards={draftCards}
                    folderActions={{
                      folders: state.folders,
                      busy: state.turnInFlight,
                      onAddFolder: (folder) => socketRef.current?.send({ type: "add_folder", folder }),
                      onTryAgain: () => sendRaw("I added the folder. Please try again."),
                    }}
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
                   * else on this page. mx-auto/max-w-[880px]/px-4 here match
                   * ChatLog.tsx's own wrapper and Composer's root exactly. */}
                  {state.error && (
                    <div className="mx-auto w-full max-w-[880px] px-4 py-1 text-sm text-red-500">{state.error}</div>
                  )}
                  {pendingQuestion && (
                    <QuestionPanel key={pendingQuestion.id} item={pendingQuestion} onAnswer={onAnswerQuestion} />
                  )}
                  {composer}
                </DetachScript.Provider></OpenDesignSystem.Provider></ViewFile.Provider></SteerActionsContext.Provider>
              ) : workflowDraft ? (
                <WorkflowDraftPage
                  threadId={workflowDraft.threadId}
                  threadTitle={workflowDraft.threadTitle}
                  nameHint={workflowDraft.nameHint}
                  initial={workflowDraft.initial}
                  revision={workflowDraft.revision}
                  onSaveRevision={saveWorkflowRevision}
                  onBack={leaveWorkflowDraft}
                  onDiscard={leaveWorkflowDraft}
                  onSave={(name, workflow) =>
                    setScheduledTaskModal({
                      task: null,
                      newWorkflow: workflow,
                      initialName: name,
                      initialEnvironment: workflowDraft.environment,
                      fromDraft: workflowDraft.draftId,
                      fromThread: workflowDraft.threadId,
                    })
                  }
                />
              ) : (
                <RunPanel
                  tasks={scheduledTasks}
                  onScheduledTasksChanged={refreshScheduledTasks}
                  selectedTask={selectedTask}
                  onSelectTask={showScheduledTaskPage}
                  onOpenTask={openScheduledTask}
                  onEditTask={(task) => setScheduledTaskModal({ task })}
                  onRunNow={runTaskNow}
                  focusStepId={focusStepId}
                  onCreateWithCoscribe={createTaskWithCoscribe}
                  onBuildWorkflow={() => setScheduledTaskModal({ task: null, newWorkflow: EMPTY_WORKFLOW })}
                />
              )}
              {scheduledTaskModal && (
                <ScheduledTaskModal
                  task={scheduledTaskModal.task}
                  draft={scheduledTaskModal.draft?.draft}
                  newWorkflow={scheduledTaskModal.newWorkflow}
                  initialName={scheduledTaskModal.initialName}
                  initialEnvironment={scheduledTaskModal.initialEnvironment}
                  fromDraft={scheduledTaskModal.fromDraft}
                  fromThread={scheduledTaskModal.fromThread}
                  onClose={() => setScheduledTaskModal(null)}
                  onSaved={onTaskSaved}
                />
              )}
              <SettingsModal
                open={settingsOpen}
                initialCategory={settingsCategory}
                startDesignSystem={settingsDesign}
                onCreateSkill={onCreateSkill}
                onClose={() => { setSettingsOpen(false); setSettingsCategory(undefined); setSettingsDesign(null); }}
              />
              {shortcutsOpen && <ShortcutsDialog onClose={() => setShortcutsOpen(false)} />}
            </div>
          }
        />
      </div>
      {/* Last in the page: the desktop shell works out which parts of the
       * window drag it in page order, so the sidebar's own buttons must
       * come after every drag area they sit on. */}
      <NavRail
        threadId={threadId}
        pinned={navPinned}
        onPinnedChange={setNavPinned}
        showToggle={pinnedItems.includes("sidebar")}
        threads={threads ?? []}
        onThreadsChanged={refreshThreads}
        onThreadRenamed={renameThreadLocally}
        mode={showingScheduled ? "run" : "create"}
        onModeChange={onNavModeChange}
        artifactsOpen={artifactsOpen}
        scheduledTasks={scheduledTasks}
        onScheduledTasksChanged={refreshScheduledTasks}
        activeTaskId={navMode === "run" ? selectedTaskId : (threadTask?.trigger_id ?? null)}
        onOpenScheduledTask={openScheduledTask}
        onEditScheduledTask={(task) => setScheduledTaskModal({ task })}
        onRunScheduledTaskNow={runTaskNow}
        onNewScheduledTask={createTaskWithCoscribe}
      />
    </div>
    </ThreadIdContext.Provider>
  );
}

export default App;
