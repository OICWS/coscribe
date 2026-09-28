/**
 * What coscribe's AI does *inside* a page or one of its frames: read it as
 * a list of elements with refs, find an element by ref, prepare a field
 * for typing, pick dropdown options, scroll. browserCdp.ts runs it in an
 * isolated world of each frame as `(${pageAgent})(action, args)`, so the
 * function must stay self-contained: no imports, no helpers outside its
 * own body. The isolated world keeps the page's own scripts from seeing
 * or replacing any of this, while sharing its DOM.
 *
 * Clicks and keystrokes are not sent from here: a DOM event dispatched by
 * script isn't trusted input and plenty of sites ignore it. This returns
 * where the element is; browserAgent.ts sends real input there.
 */

export function pageAgent(action: string, args: Record<string, unknown>): unknown {
  interface AgentState {
    refs: Map<string, WeakRef<Element>>;
    ids: WeakMap<Element, string>;
    next: number;
    /** The iframes the latest snapshot showed, by the number it gave them. */
    frames: Element[];
  }
  const w = window as unknown as { __coscribeAgent?: AgentState };
  // Refs live as long as the document: an element keeps its ref across
  // snapshots, so a ref the model read a moment ago still works if the
  // page only changed around it.
  const state: AgentState = (w.__coscribeAgent ??= { refs: new Map(), ids: new WeakMap(), next: 1, frames: [] });

  const refFor = (el: Element): string => {
    let ref = state.ids.get(el);
    if (!ref) {
      ref = `e${state.next++}`;
      state.ids.set(el, ref);
      state.refs.set(ref, new WeakRef(el));
    }
    return ref;
  };

  const clean = (text: string | null | undefined, max = 120): string => {
    const t = (text ?? "").replace(/\s+/g, " ").trim();
    return t.length > max ? `${t.slice(0, max - 1)}…` : t;
  };

  const INTERACTIVE_ROLES = new Set([
    "button", "link", "checkbox", "radio", "tab", "menuitem", "menuitemcheckbox", "menuitemradio",
    "option", "switch", "textbox", "combobox", "searchbox", "slider", "spinbutton", "treeitem",
  ]);

  const roleOf = (el: Element): string => {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit.split(" ")[0];
    const tag = el.tagName.toLowerCase();
    if (tag === "a") return el.hasAttribute("href") ? "link" : "";
    if (tag === "button" || tag === "summary") return "button";
    if (tag === "select") return (el as HTMLSelectElement).multiple ? "listbox" : "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "input") {
      const type = ((el as HTMLInputElement).type || "text").toLowerCase();
      if (type === "hidden") return "";
      if (["button", "submit", "reset", "image", "file"].includes(type)) return "button";
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "range") return "slider";
      if (type === "number") return "spinbutton";
      if (type === "search") return "searchbox";
      return "textbox";
    }
    if ((el as HTMLElement).isContentEditable && !el.parentElement?.isContentEditable) return "textbox";
    return "";
  };

  const nameOf = (el: Element): string => {
    const aria = el.getAttribute("aria-label");
    if (aria) return clean(aria);
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const text = labelledBy
        .split(" ")
        .map((id) => document.getElementById(id)?.textContent ?? "")
        .join(" ");
      if (clean(text)) return clean(text);
    }
    const tag = el.tagName.toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") {
      const id = el.getAttribute("id");
      const label =
        (id && document.querySelector(`label[for="${CSS.escape(id)}"]`)) || el.closest("label");
      if (label && clean(label.textContent)) return clean(label.textContent);
      const input = el as HTMLInputElement;
      if (["submit", "button", "reset"].includes(input.type) && input.value) return clean(input.value);
      const placeholder = el.getAttribute("placeholder");
      if (placeholder) return clean(placeholder);
      // A field's own text is its value or its options, not a name.
      return clean(el.getAttribute("title") || el.getAttribute("name"));
    }
    if (tag === "img") return clean(el.getAttribute("alt"));
    const text = clean((el as HTMLElement).innerText ?? el.textContent);
    if (text) return text;
    return clean(el.getAttribute("title") || el.querySelector("img[alt]")?.getAttribute("alt"));
  };

  const isHidden = (el: Element, style: CSSStyleDeclaration): boolean =>
    style.display === "none" ||
    style.visibility === "hidden" ||
    el.getAttribute("aria-hidden") === "true" ||
    (el as HTMLElement).hidden === true;

  const describe = (el: Element): string => {
    const role = roleOf(el) || el.tagName.toLowerCase();
    const name = nameOf(el);
    return name ? `${role} "${name}"` : role;
  };

  const find = (ref: string): Element => {
    const el = state.refs.get(ref)?.deref();
    if (!el || !el.isConnected) {
      throw new Error(`No element with ref ${ref} on this page any more -- take a new browser_snapshot.`);
    }
    return el;
  };

  const center = (el: Element): { x: number; y: number; element: string } => {
    el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" as ScrollBehavior });
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) {
      throw new Error(`${describe(el)} isn't visible on the page, so it can't be used right now.`);
    }
    const x = Math.min(Math.max(rect.left + rect.width / 2, 1), window.innerWidth - 1);
    const y = Math.min(Math.max(rect.top + rect.height / 2, 1), window.innerHeight - 1);
    return { x, y, element: describe(el) };
  };

  const SKIP_TAGS = new Set(["script", "style", "noscript", "template", "head", "meta", "link"]);

  // Smaller than this, an iframe is a tracker or an ad beacon, not
  // something to read; showing it would only cost the model tokens.
  const MIN_FRAME_WIDTH = 80;
  const MIN_FRAME_HEIGHT = 40;

  if (action === "snapshot") {
    const lines: string[] = [];
    state.frames = [];
    let buffer = "";
    const flush = () => {
      const text = clean(buffer, 400);
      if (text) lines.push(`- text: ${text}`);
      buffer = "";
    };
    let visited = 0;
    const walk = (node: Node): void => {
      if (++visited > 20000) return;
      if (node.nodeType === Node.TEXT_NODE) {
        buffer += ` ${node.textContent ?? ""}`;
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      const el = node as Element;
      const tag = el.tagName.toLowerCase();
      if (SKIP_TAGS.has(tag) || el.id === "__coscribe_agent_overlay__") return;
      const style = getComputedStyle(el);
      if (isHidden(el, style)) return;
      const block = !style.display.startsWith("inline");
      if (block) flush();

      const role = roleOf(el);
      const pointer =
        !role && style.cursor === "pointer" && el.parentElement && getComputedStyle(el.parentElement).cursor !== "pointer";
      if (INTERACTIVE_ROLES.has(role) || role === "listbox" || pointer) {
        flush();
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 || rect.height > 0) {
          const name = nameOf(el);
          let line = `- ${role || "clickable"}${name ? ` "${name}"` : ""} [ref=${refFor(el)}]`;
          const input = el as HTMLInputElement;
          if (role === "textbox" || role === "searchbox" || role === "spinbutton" || role === "combobox") {
            const value = tag === "select" ? (el as HTMLSelectElement).selectedOptions[0]?.text : input.value;
            if (value) line += ` value="${clean(value, 80)}"`;
          }
          if (tag === "input" && input.type === "file") {
            const chosen = Array.from(input.files ?? []).map((f) => f.name).join(", ");
            line += chosen ? ` [file upload] value="${clean(chosen, 80)}"` : " [file upload]";
          }
          if (role === "checkbox" || role === "radio" || role === "switch") {
            const checked = input.checked ?? el.getAttribute("aria-checked") === "true";
            line += checked ? " [checked]" : " [unchecked]";
          }
          if (input.disabled || el.getAttribute("aria-disabled") === "true") line += " [disabled]";
          const expanded = el.getAttribute("aria-expanded");
          if (expanded) line += expanded === "true" ? " [expanded]" : " [collapsed]";
          lines.push(line);
          if (tag === "select") {
            const options = Array.from((el as HTMLSelectElement).options).slice(0, 30);
            for (const option of options) lines.push(`  - option "${clean(option.text, 80)}"${option.selected ? " [selected]" : ""}`);
          }
        }
        // A link or button's own text is already its name.
        if (role !== "" || pointer) return;
      }
      if (/^h[1-6]$/.test(tag)) {
        flush();
        const text = clean((el as HTMLElement).innerText, 200);
        if (text) lines.push(`- heading "${text}" [level=${tag[1]}]`);
        return;
      }
      if (tag === "img") {
        const alt = clean(el.getAttribute("alt"));
        if (alt) lines.push(`- img "${alt}"`);
        return;
      }
      if (tag === "iframe" || tag === "frame") {
        flush();
        const rect = el.getBoundingClientRect();
        if (rect.width < MIN_FRAME_WIDTH || rect.height < MIN_FRAME_HEIGHT) return;
        // browserAgent.ts replaces the marker with the frame's own contents.
        lines.push(`- iframe "${clean(el.getAttribute("title") || el.getAttribute("src"), 80)}" @@frame:${state.frames.length}@@`);
        state.frames.push(el);
        return;
      }
      const root = (el as HTMLElement).shadowRoot;
      for (const child of Array.from(root ? root.childNodes : el.childNodes)) walk(child);
      if (block) flush();
    };
    walk(document.body ?? document.documentElement);
    flush();
    return { lines, url: location.href };
  }

  if (action === "frame_box") {
    // Where an iframe's content starts in this frame's viewport, after
    // bringing the iframe into view: its box minus border and padding.
    const el = state.frames[Number(args.index)];
    if (!el || !el.isConnected) throw new Error("That frame is gone from the page -- take a new browser_snapshot.");
    el.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" as ScrollBehavior });
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return {
      x: rect.left + el.clientLeft + parseFloat(style.paddingLeft || "0"),
      y: rect.top + el.clientTop + parseFloat(style.paddingTop || "0"),
      width: window.innerWidth,
      height: window.innerHeight,
    };
  }

  if (action === "is_file_input") {
    const el = find(String(args.ref));
    return { file: el instanceof HTMLInputElement && el.type === "file", multiple: (el as HTMLInputElement).multiple === true, element: describe(el) };
  }

  if (action === "locate") return center(find(String(args.ref)));

  if (action === "select_all_in_focus") {
    const el = document.activeElement as HTMLElement | null;
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
      el.select();
    } else if (el?.isContentEditable) {
      const range = document.createRange();
      range.selectNodeContents(el);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
    }
    return { focused: el ? describe(el) : null };
  }

  if (action === "select_option") {
    const el = find(String(args.ref));
    if (!(el instanceof HTMLSelectElement)) throw new Error(`${describe(el)} isn't a dropdown; click it instead.`);
    const wanted = (args.values as string[]).map((v) => v.trim().toLowerCase());
    const matches = Array.from(el.options).filter(
      (o) => wanted.includes(o.value.toLowerCase()) || wanted.includes(o.text.trim().toLowerCase()),
    );
    if (!matches.length) throw new Error(`None of those options are in ${describe(el)}.`);
    for (const option of Array.from(el.options)) option.selected = matches.includes(option);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { selected: matches.map((o) => o.text.trim()), element: describe(el) };
  }

  if (action === "scroll") {
    // The page itself or the scrolling box under the middle of the view --
    // many apps scroll an inner pane, not the document.
    let target: Element | null = document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2);
    while (target && target !== document.documentElement) {
      const style = getComputedStyle(target);
      if (/(auto|scroll)/.test(style.overflowY) && target.scrollHeight > target.clientHeight + 1) break;
      target = target.parentElement;
    }
    const scroller = target && target !== document.documentElement ? target : document.scrollingElement ?? document.documentElement;
    const step = Math.round(scroller.clientHeight * 0.85) * (args.direction === "up" ? -1 : 1);
    const before = scroller.scrollTop;
    scroller.scrollBy({ top: step, behavior: "instant" as ScrollBehavior });
    const after = scroller.scrollTop;
    const bottom = scroller.scrollHeight - scroller.clientHeight;
    if (after === before) return { note: args.direction === "up" ? "Already at the top." : "Already at the bottom." };
    return { note: `Scrolled ${args.direction}; now ${Math.round(after)} of ${Math.round(bottom)}px.` };
  }

  if (action === "has_text") {
    return { found: (document.body?.innerText ?? "").includes(String(args.text)) };
  }

  const overlay = (): HTMLElement => {
    let el = document.getElementById("__coscribe_agent_overlay__");
    if (!el) {
      el = document.createElement("div");
      el.id = "__coscribe_agent_overlay__";
      el.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647;";
      (document.body ?? document.documentElement).appendChild(el);
    }
    return el;
  };

  if (action === "agent_frame") {
    const el = overlay();
    el.style.boxShadow = args.on ? "inset 0 0 0 3px rgba(42,120,214,0.85), inset 0 0 24px rgba(42,120,214,0.35)" : "none";
    return {};
  }

  if (action === "show_cursor") {
    const dot = document.createElement("div");
    const x = Number(args.x);
    const y = Number(args.y);
    dot.style.cssText =
      `position:fixed;left:${x - 14}px;top:${y - 14}px;width:28px;height:28px;border-radius:50%;` +
      "background:rgba(42,120,214,0.35);border:2px solid rgb(42,120,214);pointer-events:none;" +
      "transition:transform 450ms ease-out,opacity 450ms ease-out;transform:scale(0.6);opacity:1;";
    overlay().appendChild(dot);
    requestAnimationFrame(() => {
      dot.style.transform = "scale(1.4)";
      dot.style.opacity = "0";
    });
    setTimeout(() => dot.remove(), 600);
    return {};
  }

  throw new Error(`Unknown page action ${action}`);
}
