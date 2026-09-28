/**
 * The DevTools protocol, spoken in-process through `webContents.debugger`
 * to the tabs coscribe's AI works in. It is what reads pages and frames
 * (an isolated world in each), and what catches the things plain page
 * scripts can't: alert/confirm dialogs, file choosers, and iframes from
 * other sites, which run in processes of their own. No port is opened --
 * the same reason browserAgent.ts gives for the command channel.
 *
 * Attached on a tab's first AI step, not before: a tab the user only
 * browses keeps behaving exactly as a plain one.
 */

import type { WebContents } from "electron";
import { pageAgent } from "./browserAgentPage";

const WORLD_NAME = "coscribe-agent";
const EVAL_TIMEOUT_MS = 20_000;

export interface PageDialog {
  type: string;
  message: string;
  sessionId?: string;
}

/** A frame the AI can act in: the tab's main frame or an iframe reached
 * from a snapshot. `session` is the CDP session of the process it runs in
 * (undefined for the tab's own). */
export interface AgentFrame {
  key: string;
  frameId: string;
  session?: string;
  parent?: AgentFrame;
  /** Which iframe of the parent's latest snapshot holds it. */
  index: number;
}

interface FileChooser {
  backendNodeId: number;
  session?: string;
  multiple: boolean;
}

type EvalResult = { result?: { value?: unknown; objectId?: string; description?: string }; exceptionDetails?: ExceptionDetails };
interface ExceptionDetails {
  text?: string;
  exception?: { description?: string; value?: unknown };
}

function exceptionMessage(details: ExceptionDetails): string {
  const raw = details.exception?.description ?? String(details.exception?.value ?? details.text ?? "Script error");
  // Just the sentence the model can act on, not the stack.
  return raw.split("\n")[0].replace(/^(Uncaught )?Error:\s*/, "");
}

export class TabCdp {
  dialog: PageDialog | null = null;
  chooser: FileChooser | null = null;
  readonly main: AgentFrame = { key: "", frameId: "", index: -1 };
  /** Frames by key ("f1"), and keys by frame id, so a frame keeps its key
   * (and its refs stay "f1e3") across snapshots. */
  private frames = new Map<string, AgentFrame>();
  private keys = new Map<string, string>();
  private nextKey = 1;
  /** Session of each cross-process iframe, by its frame (target) id. */
  private sessions = new Map<string, string>();
  private contexts = new Map<string, number>();

  constructor(private readonly wc: WebContents) {
    wc.debugger.on("message", (_event, method: string, params: Record<string, unknown>, sessionId?: string) =>
      this.onEvent(method, params, sessionId || undefined),
    );
    wc.debugger.on("detach", () => this.reset());
  }

  private reset(): void {
    this.dialog = null;
    this.chooser = null;
    this.sessions.clear();
    this.contexts.clear();
  }

  send<T = Record<string, unknown>>(method: string, params: Record<string, unknown> = {}, session?: string): Promise<T> {
    return this.wc.debugger.sendCommand(method, params, session) as Promise<T>;
  }

  async ensure(): Promise<void> {
    const dbg = this.wc.debugger;
    if (!dbg.isAttached()) {
      // Opening DevTools on the tab detaches us; the next step re-attaches.
      dbg.attach("1.3");
      this.reset();
      await this.enable();
      // The tab's own frame keeps its id across navigations; asked once, as
      // the renderer can't answer while a dialog holds the page.
      const tree = await this.send<{ frameTree: { frame: { id: string } } }>("Page.getFrameTree");
      this.main.frameId = tree.frameTree.frame.id;
    }
  }

  private async enable(session?: string): Promise<void> {
    await this.send("Page.enable", {}, session);
    await this.send("Target.setAutoAttach", { autoAttach: true, waitForDebuggerOnStart: false, flatten: true }, session);
  }

  private onEvent(method: string, params: Record<string, unknown>, session?: string): void {
    if (method === "Page.javascriptDialogOpening") {
      this.dialog = {
        type: String(params.type),
        message: String(params.message ?? ""),
        sessionId: session,
      };
    } else if (method === "Page.javascriptDialogClosed") {
      this.dialog = null;
    } else if (method === "Page.fileChooserOpened") {
      this.chooser = {
        backendNodeId: Number(params.backendNodeId),
        session,
        multiple: params.mode === "selectMultiple",
      };
    } else if (method === "Target.attachedToTarget") {
      const info = params.targetInfo as { targetId: string; type: string };
      const child = String(params.sessionId);
      if (info.type === "iframe") {
        this.sessions.set(info.targetId, child);
        void this.enable(child).catch(() => undefined);
      }
    } else if (method === "Target.detachedFromTarget") {
      for (const [frameId, s] of this.sessions) if (s === params.sessionId) this.sessions.delete(frameId);
    } else if (method === "Page.frameNavigated" || method === "Page.frameDetached") {
      const frame = (params.frame as { id?: string } | undefined)?.id ?? String(params.frameId ?? "");
      for (const key of this.contexts.keys()) if (key.endsWith(`/${frame}`)) this.contexts.delete(key);
      if (frame === this.main.frameId) {
        this.frames.clear();
        this.keys.clear();
      }
    }
  }

  private contextKey(frame: AgentFrame): string {
    return `${frame.session ?? ""}/${frame.frameId}`;
  }

  private async context(frame: AgentFrame): Promise<number> {
    const key = this.contextKey(frame);
    const known = this.contexts.get(key);
    if (known !== undefined) return known;
    const { executionContextId } = await this.send<{ executionContextId: number }>(
      "Page.createIsolatedWorld",
      { frameId: frame.frameId, worldName: WORLD_NAME },
      frame.session,
    );
    this.contexts.set(key, executionContextId);
    return executionContextId;
  }

  /** Runs `expression` in the frame's isolated world; by value unless
   * `asObject`, which returns a remote object id instead. */
  async evaluate(frame: AgentFrame, expression: string, asObject = false): Promise<EvalResult["result"]> {
    for (let attempt = 0; ; attempt++) {
      const contextId = await this.context(frame);
      try {
        const reply = await this.send<EvalResult>(
          "Runtime.evaluate",
          { expression, contextId, returnByValue: !asObject, awaitPromise: true, timeout: EVAL_TIMEOUT_MS },
          frame.session,
        );
        if (reply.exceptionDetails) throw new Error(exceptionMessage(reply.exceptionDetails));
        return reply.result;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        // The world goes with the document it was made in.
        if (attempt === 0 && /context/i.test(message) && /(find|destroyed|not found)/i.test(message)) {
          this.contexts.delete(this.contextKey(frame));
          continue;
        }
        throw err instanceof Error ? err : new Error(message);
      }
    }
  }

  async agent<T>(frame: AgentFrame, action: string, args: Record<string, unknown> = {}): Promise<T> {
    const code = `(${pageAgent.toString()})(${JSON.stringify(action)}, ${JSON.stringify(args)})`;
    return (await this.evaluate(frame, code))?.value as T;
  }

  /** The frame shown by iframe number `index` of `parent`'s latest snapshot. */
  async childFrame(parent: AgentFrame, index: number): Promise<AgentFrame | null> {
    const handle = await this.evaluate(parent, `window.__coscribeAgent?.frames?.[${index}] ?? null`, true);
    if (!handle?.objectId) return null;
    const { node } = await this.send<{ node: { frameId?: string } }>(
      "DOM.describeNode",
      { objectId: handle.objectId },
      parent.session,
    );
    const frameId = node.frameId;
    if (!frameId) return null;
    let key = this.keys.get(frameId);
    if (!key) {
      key = `f${this.nextKey++}`;
      this.keys.set(frameId, key);
    }
    const frame: AgentFrame = { key, frameId, session: this.sessions.get(frameId) ?? parent.session, parent, index };
    this.frames.set(key, frame);
    return frame;
  }

  frame(key: string): AgentFrame {
    if (!key) return this.main;
    const frame = this.frames.get(key);
    if (!frame) throw new Error(`No frame ${key} on this page any more -- take a new browser_snapshot.`);
    return frame;
  }

  /** The remote object for element `ref` in `frame`, for DOM commands. */
  async element(frame: AgentFrame, ref: string): Promise<string> {
    const handle = await this.evaluate(
      frame,
      `(() => { const el = window.__coscribeAgent?.refs.get(${JSON.stringify(ref)})?.deref();
        if (!el || !el.isConnected) throw new Error("No element with ref ${ref} on this page any more -- take a new browser_snapshot.");
        return el; })()`,
      true,
    );
    if (!handle?.objectId) throw new Error(`No element with ref ${ref}.`);
    return handle.objectId;
  }

  /** Waits for `command`, or until the page puts up a dialog: a dialog
   * holds the page -- and any command waiting on it -- until someone
   * answers it. Undefined in that case. */
  private async unlessDialog<T>(command: Promise<T>): Promise<T | undefined> {
    let done = false;
    let value: T | undefined;
    let error: unknown;
    void command.then(
      (v) => {
        done = true;
        value = v;
      },
      (err: unknown) => {
        done = true;
        error = err;
      },
    );
    while (!done && !this.dialog) await new Promise((resolve) => setTimeout(resolve, 20));
    if (error) throw error instanceof Error ? error : new Error(String(error));
    return value;
  }

  async input(method: string, params: Record<string, unknown>): Promise<void> {
    await this.unlessDialog(this.send(method, params));
  }

  async handleDialog(accept: boolean): Promise<PageDialog> {
    const dialog = this.dialog;
    if (!dialog) throw new Error("No dialog is open on this page.");
    await this.send("Page.handleJavaScriptDialog", { accept }, dialog.sessionId);
    // Electron shows its own message box for the dialog too, and closes it
    // only on this internal event (lib/browser/api/web-contents.ts); an
    // answer through the protocol would otherwise leave it on screen.
    this.wc.emit("-cancel-dialogs");
    this.dialog = null;
    return dialog;
  }

  async interceptFileChooser(enabled: boolean): Promise<void> {
    const sessions = [undefined, ...this.sessions.values()];
    await Promise.all(
      sessions.map((s) => this.send("Page.setInterceptFileChooserDialog", { enabled }, s).catch(() => undefined)),
    );
  }

  /** Runs `expression` in the page's own main world, as a page script
   * would. `opened` when the script put up a dialog before finishing. */
  async evaluateInPage(expression: string): Promise<{ value?: unknown; opened?: boolean }> {
    const reply = await this.unlessDialog(
      this.send<EvalResult>("Runtime.evaluate", {
        expression,
        returnByValue: true,
        awaitPromise: true,
        userGesture: true,
        timeout: EVAL_TIMEOUT_MS,
      }),
    );
    if (!reply) return { opened: true };
    if (reply.exceptionDetails) throw new Error(exceptionMessage(reply.exceptionDetails));
    const result = reply.result ?? {};
    return { value: "value" in result ? result.value : result.description };
  }
}

const cdps = new WeakMap<WebContents, TabCdp>();

export function cdpFor(wc: WebContents): TabCdp {
  let cdp = cdps.get(wc);
  if (!cdp) {
    cdp = new TabCdp(wc);
    cdps.set(wc, cdp);
  }
  return cdp;
}
