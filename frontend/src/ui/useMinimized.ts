import { useEffect, useState } from "react";

/** Persist minimize state in sessionStorage. */
export function useMinimized(key: string, defaultMinimized = false) {
  const storageKey = `ops.minimize.${key}`;
  const [minimized, setMinimized] = useState(() => {
    try {
      const v = sessionStorage.getItem(storageKey);
      if (v === "1") return true;
      if (v === "0") return false;
    } catch {
      /* ignore */
    }
    return defaultMinimized;
  });

  useEffect(() => {
    try {
      sessionStorage.setItem(storageKey, minimized ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [minimized, storageKey]);

  return [minimized, setMinimized] as const;
}
