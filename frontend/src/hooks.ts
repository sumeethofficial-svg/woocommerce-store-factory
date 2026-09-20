import { useCallback, useEffect, useState } from "react";
import { ApiError, api, messageOf } from "./api";
import { isActive, type Store, type Usage } from "./types";

const ACTIVE_INTERVAL_MS = 3000;
const IDLE_INTERVAL_MS = 20000;

export function useStores(onUnauthorized: () => void) {
  const [stores, setStores] = useState<Store[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextStores, nextUsage] = await Promise.all([api.listStores(), api.usage()]);
      setStores(nextStores);
      setUsage(nextUsage);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized();
      else setError(messageOf(err));
    } finally {
      setLoading(false);
    }
  }, [onUnauthorized]);

  const active = stores.some((store) => isActive(store.status));

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), active ? ACTIVE_INTERVAL_MS : IDLE_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refresh, active]);

  return { stores, usage, loading, error, refresh };
}
