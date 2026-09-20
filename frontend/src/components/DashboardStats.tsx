import type { Usage } from "../types";

interface Props {
  usage: Usage | null;
  username: string;
  onSignOut: () => void;
}

export default function DashboardStats({ usage, username, onSignOut }: Props) {
  const stores = usage ? `${usage.stores.used} / ${usage.stores.limit}` : "-";
  const storage = usage ? `${usage.storage_gi.used} / ${usage.storage_gi.limit}` : "-";
  const storesFull = usage ? usage.stores.used >= usage.stores.limit : false;
  const storageFull = usage ? usage.storage_gi.used >= usage.storage_gi.limit : false;

  return (
    <div className="stats">
      <div className="stat" data-full={storesFull}>
        <span className="stat-value">{stores}</span>
        <span className="stat-label">Stores</span>
      </div>
      <div className="stat" data-full={storageFull}>
        <span className="stat-value">{storage} Gi</span>
        <span className="stat-label">WordPress storage</span>
      </div>
      <div className="stat-user">
        <span>{username}</span>
        <button className="link" onClick={onSignOut}>
          Sign out
        </button>
      </div>
    </div>
  );
}
