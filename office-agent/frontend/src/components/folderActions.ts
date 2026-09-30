import { createContext } from "react";

/** What the card under a refused script write can do: the folders the
 * conversation has, and how to change them. Unset outside a chat, where
 * no card is shown. */
export interface FolderActions {
  folders: string[];
  /** A reply is being generated; folders can't change meanwhile. */
  busy: boolean;
  onAddFolder: (folder: string) => void;
  onTryAgain: () => void;
}

export const FolderActionsContext = createContext<FolderActions | null>(null);
