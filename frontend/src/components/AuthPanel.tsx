import { useState, type FormEvent } from "react";
import { api, messageOf, tokenStore } from "../api";

interface Props {
  onAuthenticated: () => void;
}

export default function AuthPanel({ onAuthenticated }: Props) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const registering = mode === "register";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (registering) await api.register(username, email, password);
      const { access_token } = await api.login(username, password);
      tokenStore.set(access_token);
      onAuthenticated();
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth">
      <section className="auth-intro">
        <h1>Store Factory</h1>
        <p>
          Launch a WooCommerce store in a few minutes. Every store gets its own database and its
          own isolated space in the cluster, so one store never touches another.
        </p>
      </section>
      <form className="panel auth-form" onSubmit={submit}>
        <h2>{registering ? "Create your account" : "Sign in"}</h2>
        <label>
          Username
          <input
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value.toLowerCase())}
            minLength={3}
            maxLength={32}
            pattern="[a-z0-9_]{3,32}"
            title="3 to 32 lowercase letters, numbers or underscores"
            spellCheck={false}
            required
          />
        </label>
        {registering && (
          <label>
            Email
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <span className="hint">Used as the admin email of your stores.</span>
          </label>
        )}
        <label>
          Password
          <input
            type="password"
            autoComplete={registering ? "new-password" : "current-password"}
            minLength={8}
            maxLength={64}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          {registering && <span className="hint">8 to 64 characters.</span>}
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button className="primary" disabled={busy}>
          {busy ? "Please wait" : registering ? "Create account" : "Sign in"}
        </button>
        <button
          type="button"
          className="link"
          onClick={() => {
            setMode(registering ? "login" : "register");
            setError(null);
          }}
        >
          {registering ? "I already have an account" : "Create an account"}
        </button>
      </form>
    </main>
  );
}
