import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, messageOf } from "../api";
import type { Usage } from "../types";

const NAME_PATTERN = /^[a-z][a-z0-9-]{1,28}[a-z0-9]$/;
const PASSWORD_PATTERN = /^[\x21-\x7e]{8,64}$/;
const PRICE_PATTERN = /^\d{1,8}(\.\d{1,2})?$/;
const MAX_PRODUCTS = 20;
const CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*";

export function generatePassword(length = 16): string {
  const bytes = new Uint32Array(length);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => CHARSET[value % CHARSET.length]).join("");
}

interface ProductRow {
  key: number;
  name: string;
  price: string;
  description: string;
}

const EXAMPLE_PRODUCTS = [
  { name: "Classic T-Shirt", price: "499", description: "A classic cotton t-shirt" },
  { name: "Denim Jeans", price: "1299", description: "Blue denim jeans" },
  { name: "Sneakers", price: "2499", description: "Comfortable running shoes" },
];

interface Props {
  usage: Usage;
  onClose: () => void;
  onCreated: () => void;
}

export default function LaunchStoreModal({ usage, onClose, onCreated }: Props) {
  const nextKey = useRef(EXAMPLE_PRODUCTS.length);
  const [name, setName] = useState("");
  const [password, setPassword] = useState(() => generatePassword());
  const [copied, setCopied] = useState(false);
  const [storage, setStorage] = useState("1");
  const [products, setProducts] = useState<ProductRow[]>(
    EXAMPLE_PRODUCTS.map((product, key) => ({ key, ...product })),
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const storesLeft = usage.stores.limit - usage.stores.used;
  const storageLeft = usage.storage_gi.limit - usage.storage_gi.used;
  const maxStorage = Math.min(usage.max_store_storage_gi, storageLeft);
  const storageValue = Number(storage);
  const storageValid = Number.isInteger(storageValue) && storageValue >= 1 && storageValue <= maxStorage;
  const nameValid = NAME_PATTERN.test(name);
  const passwordValid = PASSWORD_PATTERN.test(password);
  const filled = products.filter((row) => row.name.trim() || row.price.trim() || row.description.trim());
  const productsValid = filled.every((row) => row.name.trim() && PRICE_PATTERN.test(row.price.trim()));
  const blocked = storesLeft < 1 || maxStorage < 1;
  const canSubmit = !blocked && nameValid && passwordValid && storageValid && productsValid && !busy;

  const updateProduct = (key: number, field: keyof Omit<ProductRow, "key">, value: string) =>
    setProducts((rows) => rows.map((row) => (row.key === key ? { ...row, [field]: value } : row)));

  const copyPassword = async () => {
    await navigator.clipboard?.writeText(password);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      await api.createStore({
        name,
        admin_password: password,
        storage_gi: storageValue,
        products: filled.map((row) => ({
          name: row.name.trim(),
          price: row.price.trim(),
          description: row.description.trim(),
        })),
      });
      onCreated();
      onClose();
    } catch (err) {
      setError(messageOf(err));
      setBusy(false);
    }
  };

  return (
    <div className="backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <form
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="launch-title"
        onSubmit={submit}
      >
        <header className="modal-head">
          <h2 id="launch-title">Launch new store</h2>
          <button type="button" className="icon" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>

        <div className="modal-body">
          {blocked && (
            <p className="error" role="alert">
              {storesLeft < 1
                ? `You have reached the limit of ${usage.stores.limit} stores.`
                : "You have used all of your WordPress storage."}
            </p>
          )}

          <label>
            Store name
            <input
              value={name}
              onChange={(e) => setName(e.target.value.toLowerCase())}
              placeholder="my-shop"
              maxLength={30}
              spellCheck={false}
              autoComplete="off"
              aria-invalid={name.length > 0 && !nameValid}
              autoFocus
            />
            <span className={name && !nameValid ? "hint invalid" : "hint"}>
              {name && !nameValid
                ? "Use 3 to 30 lowercase letters, numbers or hyphens. Start with a letter and end with a letter or number."
                : name
                  ? `Runs in the namespace store-${name}.`
                  : "The name becomes the store's address and its isolated namespace."}
            </span>
          </label>

          <div className="field">
            <label htmlFor="admin-password">Admin password</label>
            <div className="password-row">
              <input
                id="admin-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                aria-invalid={!passwordValid}
              />
              <button type="button" className="ghost" onClick={() => setPassword(generatePassword())}>
                Generate
              </button>
              <button type="button" className="ghost" onClick={copyPassword}>
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            <span className={passwordValid ? "hint" : "hint invalid"}>
              {passwordValid
                ? "This will be the password for the admin user."
                : "Use 8 to 64 characters with no spaces."}
            </span>
          </div>

          <label>
            WordPress storage (Gi)
            <input
              type="number"
              min={1}
              max={Math.max(maxStorage, 1)}
              step={1}
              value={storage}
              onChange={(e) => setStorage(e.target.value)}
              aria-invalid={!storageValid}
            />
            <span className={storageValid ? "hint" : "hint invalid"}>
              {storageValid
                ? `MySQL needs an additional ${usage.mysql_storage_gi} Gi. Total: ${storageValue + usage.mysql_storage_gi} Gi. You have ${storageLeft} Gi of quota left.`
                : `Choose a whole number from 1 to ${Math.max(maxStorage, 1)} Gi.`}
            </span>
          </label>

          <fieldset className="products">
            <legend>Initial products</legend>
            {products.map((row) => (
              <div className="product-row" key={row.key}>
                <input
                  aria-label="Product name"
                  placeholder="Name"
                  maxLength={100}
                  value={row.name}
                  onChange={(e) => updateProduct(row.key, "name", e.target.value)}
                />
                <input
                  aria-label="Price"
                  placeholder="Price"
                  inputMode="decimal"
                  value={row.price}
                  onChange={(e) => updateProduct(row.key, "price", e.target.value)}
                  aria-invalid={row.price.trim() !== "" && !PRICE_PATTERN.test(row.price.trim())}
                />
                <input
                  aria-label="Description"
                  placeholder="Description"
                  maxLength={500}
                  value={row.description}
                  onChange={(e) => updateProduct(row.key, "description", e.target.value)}
                />
                <button
                  type="button"
                  className="icon"
                  aria-label={`Remove ${row.name || "product"}`}
                  onClick={() => setProducts((rows) => rows.filter((r) => r.key !== row.key))}
                >
                  ×
                </button>
              </div>
            ))}
            <button
              type="button"
              className="ghost"
              disabled={products.length >= MAX_PRODUCTS}
              onClick={() =>
                setProducts((rows) => [
                  ...rows,
                  { key: nextKey.current++, name: "", price: "", description: "" },
                ])
              }
            >
              Add product
            </button>
            {!productsValid && (
              <span className="hint invalid">Each product needs a name and a price such as 499 or 12.50.</span>
            )}
          </fieldset>

          <p className="hint">
            {usage.online_payments
              ? "Checkout offers Cash on delivery and Razorpay test payments (UPI, cards)."
              : "Checkout offers Cash on delivery. Razorpay test payments are not configured on this server."}
          </p>

          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
        </div>

        <footer className="modal-foot">
          <button type="button" className="ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={!canSubmit}>
            {busy ? "Launching" : "Launch store"}
          </button>
        </footer>
      </form>
    </div>
  );
}
