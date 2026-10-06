import { useCallback, useEffect, useState } from "react";
import { getCodeStatus, installCode, removeCode } from "./rest";
import type { CodeStatus } from "../types/settings";

/** The code module's download, re-read while one is under way (the
 * download request itself only answers once it's done). */
export function useCodeStatus() {
  const [status, setStatus] = useState<CodeStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    getCodeStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  useEffect(refresh, [refresh]);

  const preparing = busy || Boolean(status?.preparing);
  useEffect(() => {
    if (!preparing) return;
    const timer = window.setInterval(refresh, 1000);
    return () => window.clearInterval(timer);
  }, [preparing, refresh]);

  const run = async (action: typeof installCode) => {
    setBusy(true);
    setError(null);
    const result = await action();
    setBusy(false);
    if ("error" in result) setError(result.error);
    else setStatus(result);
  };

  return {
    status,
    error,
    preparing,
    refresh,
    install: () => run(installCode),
    remove: () => run(removeCode),
  };
}
