/** REST shapes outside the settings panel: threads, slash commands, file
 * uploads. Hand-encoded from a direct read of coscribe/web/app.py, same
 * discipline as wire.ts and settings.ts. */

export interface ThreadSummary {
  thread_id: string;
  updated_at: string | null;
  message_count: number;
  preview: string;
  workspace_root: string;
}

export type ThreadsResponse = ThreadSummary[];

export type ThreadDeleteResult = { deleted: string } | { error: string };

export type ThreadRenameResult = { thread_id: string; title: string } | { error: string };

export interface CommandInfo {
  name: string;
  description: string;
}

export type CommandsResponse = CommandInfo[];

export type UploadResult = { path: string; bytes_written: number } | { error: string };
