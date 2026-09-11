import { useState } from "react";
import { CheckIcon, CopyIcon } from "./icons";

interface CopyButtonProps {
  getText: () => string;
  title?: string;
  className: string;
}

/** A small button that copies `getText()`'s result to the clipboard, with
 * a brief checkmark confirmation -- shared by the agent-message-level copy
 * button (ChatLog.tsx) and the per-code-block one (Markdown.tsx), the two
 * places this app renders text a user would want to lift out verbatim. */
export function CopyButton({ getText, title = "Copy", className }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleClick = async () => {
    try {
      await navigator.clipboard.writeText(getText());
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard permission denied, or navigator.clipboard unavailable
      // (e.g. non-HTTPS/non-localhost context) -- fails silently, same
      // graceful-degradation posture the rest of this app's non-critical
      // UI already has (e.g. a skipped preview thumbnail).
    }
  };

  return (
    <button type="button" title={copied ? "Copied" : title} onClick={handleClick} className={className}>
      {copied ? <CheckIcon className="h-3.5 w-3.5" /> : <CopyIcon className="h-3.5 w-3.5" />}
    </button>
  );
}
