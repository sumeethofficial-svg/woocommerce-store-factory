import { useEffect, useState } from "react";
import { api } from "../api";
import { isActive, type Store, type StoreEvent } from "../types";

const formatTime = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export default function StoreDetails({ store }: { store: Store }) {
  const [events, setEvents] = useState<StoreEvent[]>([]);
  const active = isActive(store.status);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .events(store.id)
        .then((data) => !cancelled && setEvents(data))
        .catch(() => undefined);
    void load();
    const timer = active ? setInterval(load, 3000) : undefined;
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [store.id, store.status, active]);

  return (
    <div className="details">
      {store.error && <pre className="failure">{store.error}</pre>}
      <ol className="events">
        {events.map((event) => (
          <li key={event.id} data-level={event.level}>
            <time>{formatTime(event.created_at)}</time>
            <span>{event.message}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
