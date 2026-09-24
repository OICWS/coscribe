/** localStorage that never throws -- it's unavailable in some private
 * windows and embedded webviews, and a remembered UI preference is never
 * worth breaking the page over. */
export function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStored(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Not persisted; the in-memory state still applies for this visit.
  }
}
