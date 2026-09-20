import { useCallback, useEffect, useState } from "react";
import { api, tokenStore } from "./api";
import AuthPanel from "./components/AuthPanel";
import DashboardStats from "./components/DashboardStats";
import LaunchStoreModal from "./components/LaunchStoreModal";
import StoreRow from "./components/StoreRow";
import { useStores } from "./hooks";
import type { User } from "./types";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(Boolean(tokenStore.get()));

  const signOut = useCallback(() => {
    tokenStore.clear();
    setUser(null);
  }, []);

  const loadUser = useCallback(() => {
    setChecking(true);
    api
      .me()
      .then(setUser)
      .catch(signOut)
      .finally(() => setChecking(false));
  }, [signOut]);

  useEffect(() => {
    if (tokenStore.get()) loadUser();
  }, [loadUser]);

  if (checking) return null;
  if (!user) return <AuthPanel onAuthenticated={loadUser} />;
  return <Dashboard user={user} onSignOut={signOut} />;
}

function Dashboard({ user, onSignOut }: { user: User; onSignOut: () => void }) {
  const { stores, usage, loading, error, refresh } = useStores(onSignOut);
  const [launching, setLaunching] = useState(false);

  const full = usage
    ? usage.stores.used >= usage.stores.limit || usage.storage_gi.used >= usage.storage_gi.limit
    : true;
  const fullReason = usage && usage.stores.used >= usage.stores.limit
    ? "You have reached your store limit"
    : "You have used all of your storage";

  return (
    <>
      <header className="topbar">
        <strong>Store Factory</strong>
        <DashboardStats usage={usage} username={user.username} onSignOut={onSignOut} />
      </header>
      <main className="dashboard">
        <section>
          <div className="section-head">
            <h2>Your stores</h2>
            <button
              className="primary"
              disabled={full}
              title={full ? fullReason : undefined}
              onClick={() => setLaunching(true)}
            >
              New store
            </button>
          </div>
          {error && (
            <p className="error" role="alert">
              Could not reach the server: {error}
            </p>
          )}
          {!loading && stores.length === 0 && !error && (
            <p className="empty">
              No stores yet. Choose New store and it will be live in a few minutes.
            </p>
          )}
          <ul className="stores">
            {stores.map((store) => (
              <StoreRow key={store.id} store={store} onChanged={refresh} />
            ))}
          </ul>
        </section>
      </main>
      {launching && usage && (
        <LaunchStoreModal usage={usage} onClose={() => setLaunching(false)} onCreated={refresh} />
      )}
    </>
  );
}
