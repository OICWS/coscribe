import { useCallback, useEffect, useState } from "react";
import type { CodePermissions, CodePolicy } from "../types/settings";
import { getCodePermissions, setCodePermission } from "./rest";

/** Settings > Code's "without asking" switches; a change is saved at once. */
export function useCodePermissions() {
  const [permissions, setPermissions] = useState<CodePermissions | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCodePermissions()
      .then(setPermissions)
      .catch(() => setPermissions(null));
  }, []);

  const set = useCallback(async (action: keyof CodePermissions, policy: CodePolicy) => {
    setError(null);
    const result = await setCodePermission(action, policy);
    if ("error" in result) setError(result.error);
    else setPermissions(result);
  }, []);

  return { permissions, error, set };
}
