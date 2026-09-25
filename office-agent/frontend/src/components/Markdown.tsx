import { isValidElement, useRef, type ClipboardEvent, type ComponentPropsWithoutRef, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import { CopyButton } from "./CopyButton";

const hoverCopy =
  "flex h-6 w-6 items-center justify-center rounded-md text-[var(--muted)] opacity-0 transition-opacity hover:bg-[var(--card-bg)] hover:text-[var(--fg)] focus-visible:opacity-100";

function languageOf(children: ReactNode): string | null {
  if (!isValidElement<{ className?: string }>(children)) return null;
  const match = /language-([\w+#-]+)/.exec(children.props.className ?? "");
  return match ? match[1] : null;
}

/** react-markdown passes a `node` prop (the hast node) to every override;
 * it isn't a DOM attribute, so it's pulled out before spreading. */
function CodeBlock({ node: _node, children, ...props }: ComponentPropsWithoutRef<"pre"> & { node?: unknown }) {
  const preRef = useRef<HTMLPreElement>(null);
  return (
    <div className="code-block group/code">
      <div className="flex h-8 items-center justify-between pl-3.5 pr-1.5 text-xs text-[var(--muted)]">
        <span className="font-mono">{languageOf(children) ?? "text"}</span>
        <CopyButton
          getText={() => preRef.current?.textContent ?? ""}
          title="Copy code"
          className={`${hoverCopy} group-hover/code:opacity-100`}
        />
      </div>
      <pre ref={preRef} {...props}>
        {children}
      </pre>
    </div>
  );
}

/** Tab-separated, so it pastes into Excel or Sheets as cells. */
function tableAsTsv(table: HTMLTableElement): string {
  return [...table.rows]
    .map((row) => [...row.cells].map((cell) => (cell.textContent ?? "").replace(/\s+/g, " ").trim()).join("\t"))
    .join("\n");
}

function Table({ node: _node, ...props }: ComponentPropsWithoutRef<"table"> & { node?: unknown }) {
  const tableRef = useRef<HTMLTableElement>(null);
  return (
    <div className="group/table relative">
      <div className="overflow-x-auto">
        <table ref={tableRef} {...props} />
      </div>
      <CopyButton
        getText={() => (tableRef.current ? tableAsTsv(tableRef.current) : "")}
        title="Copy table"
        className={`${hoverCopy} absolute right-0 top-1 group-hover/table:opacity-100`}
      />
    </div>
  );
}

function texOf(katex: Element): string | null {
  return katex.querySelector('annotation[encoding="application/x-tex"]')?.textContent ?? null;
}

function MathSpan({ node: _node, className, ...props }: ComponentPropsWithoutRef<"span"> & { node?: unknown }) {
  const ref = useRef<HTMLSpanElement>(null);
  if (!className?.split(" ").includes("katex-display")) return <span className={className} {...props} />;
  return (
    <span className="group/math relative block">
      <span ref={ref} className={className} {...props} />
      <CopyButton
        getText={() => {
          const katex = ref.current?.querySelector(".katex");
          return katex ? (texOf(katex) ?? "") : "";
        }}
        title="Copy LaTeX"
        className={`${hoverCopy} absolute right-0 top-0 group-hover/math:opacity-100`}
      />
    </span>
  );
}

/** Copying a selection that contains rendered math puts its LaTeX source
 * on the clipboard, instead of KaTeX's doubled MathML-plus-glyphs text. */
function copyWithTex(event: ClipboardEvent<HTMLDivElement>) {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return;
  const fragment = selection.getRangeAt(0).cloneContents();
  const maths = fragment.querySelectorAll(".katex");
  if (maths.length === 0) return;
  for (const math of maths) {
    const tex = texOf(math);
    if (tex === null) continue;
    const display = math.parentElement?.classList.contains("katex-display") ?? false;
    math.replaceWith(display ? `$$${tex}$$` : `$${tex}$`);
  }
  // innerText (unlike textContent) keeps paragraph breaks, but only for an
  // element that's laid out.
  const holder = document.createElement("div");
  holder.style.cssText = "position:fixed;left:-99999px;top:0;white-space:pre-wrap";
  holder.appendChild(fragment);
  document.body.appendChild(holder);
  const text = holder.innerText;
  holder.remove();
  event.clipboardData.setData("text/plain", text);
  event.preventDefault();
}

/** Assistant text as GitHub-flavored markdown plus LaTeX math. No
 * rehype-raw: raw HTML in a message is never rendered as markup. */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="prose prose-sm md-prose max-w-none" onCopy={copyWithTex}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{ pre: CodeBlock, table: Table, span: MathSpan }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
