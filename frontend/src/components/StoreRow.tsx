import { useEffect, useState } from "react";
import { api, messageOf } from "../api";
import type { AdminCredentials, Store } from "../types";
import Pipeline from "./Pipeline";
import StoreDetails from "./StoreDetails";

interface Props {
  store: Store;
  onChanged: () => void;
}

const STATUS_LABEL: Record<Store["status"], string> = {
  requested: "Requested",
  provisioning: "Provisioning",
  initializing: "Initializing",
  ready: "Ready",
  failed: "Failed",
  deleting: "Deleting",
  deleted: "Deleted",
};

function Credentials({ store }: { store: Store }) {
  const [credentials, setCredentials] = useState<AdminCredentials | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [visible, setVisible] = useState(false);
  const [copied, setCopied] = useState(false);
  const ready = store.status === "ready";

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    api
      .credentials(store.id)
      .then((data) => !cancelled && setCredentials(data))
      .catch((err) => !cancelled && setError(messageOf(err)));
    return () => {
      cancelled = true;
    };
  }, [store.id, ready]);

  const copy = async () => {
    if (!credentials) return;
    await navigator.clipboard?.writeText(credentials.password);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="credentials">
      <h4>Admin credentials</h4>
      {!ready && <p className="hint">Available once the store is ready.</p>}
      {ready && error && <p className="error">{error}</p>}
      {ready && credentials && (
        <dl>
          <dt>User</dt>
          <dd className="mono">{credentials.username}</dd>
          <dt>Password</dt>
          <dd className="mono secret">{visible ? credentials.password : "••••••••••••"}</dd>
          <dd className="cred-actions">
            <button className="ghost" onClick={() => setVisible(!visible)}>
              {visible ? "Hide" : "Show"}
            </button>
            <button className="ghost" onClick={copy}>
              {copied ? "Copied" : "Copy"}
            </button>
          </dd>
        </dl>
      )}
    </div>
  );
}

export default function StoreRow({ store, onChanged }: Props) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      setConfirming(false);
      onChanged();
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  };

  const deleting = store.status === "deleting";
  const ready = store.status === "ready";

  return (
    <li className="store" data-status={store.status}>
      <div className="store-head">
        <h3>{store.name}</h3>
        <span className="status" data-status={store.status}>
          {STATUS_LABEL[store.status]}
        </span>
      </div>

      <dl className="facts">
        <dt>Store URL</dt>
        <dd>
          {ready && store.url ? (
            <a href={store.url} target="_blank" rel="noreferrer">
              {store.url}
            </a>
          ) : (
            <span className="muted">Available once ready</span>
          )}
        </dd>
        <dt>Admin URL</dt>
        <dd>
          {ready && store.admin_url ? (
            <a href={store.admin_url} target="_blank" rel="noreferrer">
              {store.admin_url}
            </a>
          ) : (
            <span className="muted">Available once ready</span>
          )}
        </dd>
        <dt>Namespace</dt>
        <dd className="mono">{store.namespace}</dd>
        <dt>Owner</dt>
        <dd>{store.owner}</dd>
        <dt>Storage</dt>
        <dd>{store.storage_gi} Gi</dd>
        <dt>Created</dt>
        <dd>{new Date(store.created_at).toLocaleString()}</dd>
      </dl>

      <Pipeline status={store.status} />
      <Credentials store={store} />

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <div className="actions">
        {store.status === "failed" && (
          <button className="ghost" disabled={busy} onClick={() => run(() => api.retryStore(store.id))}>
            Retry
          </button>
        )}
        <button className="ghost" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Hide details" : "Details"}
        </button>
        {!deleting &&
          (confirming ? (
            <span className="confirm">
              <button className="danger" disabled={busy} onClick={() => run(() => api.deleteStore(store.id))}>
                Delete permanently
              </button>
              <button className="ghost" onClick={() => setConfirming(false)}>
                Keep
              </button>
            </span>
          ) : (
            <button className="ghost danger-text" onClick={() => setConfirming(true)}>
              Delete
            </button>
          ))}
      </div>
      {open && <StoreDetails store={store} />}
    </li>
  );
}
