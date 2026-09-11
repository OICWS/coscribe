import { useRef, type ComponentPropsWithoutRef } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import { CopyButton } from "./CopyButton";

/** react-markdown injects a `node` prop (the underlying hast node) into
 * every custom component override -- real, but not a valid DOM attribute,
 * so it must be destructured out before the rest spreads onto the actual
 * `<pre>` element (otherwise React warns about an unrecognized DOM prop). */
function CodeBlock({ node: _node, ...props }: ComponentPropsWithoutRef<"pre"> & { node?: unknown }) {
  const preRef = useRef<HTMLPreElement>(null);
  return (
    <div className="group relative">
      <pre ref={preRef} {...props} />
      <CopyButton
        getText={() => preRef.current?.textContent ?? ""}
        title="Copy code"
        className="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-md border border-[var(--border)] bg-[var(--bg)] text-[var(--muted)] opacity-0 transition-opacity hover:text-[var(--fg)] group-hover:opacity-100"
      />
    </div>
  );
}

/** Renders assistant chat text as GitHub-flavored markdown (headings,
 * lists, tables, code fences, links) plus LaTeX math ($inline$ and
 * $$block$$, via remark-math/rehype-katex) -- matches how claude.ai's own
 * chat surface renders assistant output, instead of showing raw markdown/
 * LaTeX source. Deliberately not used for the user's own bubble (their
 * typed text renders as typed, unparsed, the same convention claude.ai
 * itself uses) or for the short tool/system status line.
 *
 * No rehype-raw plugin -- react-markdown never renders raw HTML found in
 * the text by default, so a message containing HTML-like content can't
 * inject markup into the page. */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="prose prose-sm md-prose max-w-none">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{ pre: CodeBlock }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
