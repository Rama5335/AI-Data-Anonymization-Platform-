import { useState } from "react";
import { useAuth } from "../auth/AuthContext";

export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      // The AuthContext's login() updates its in-memory state to
      // "authenticated", which re-renders App into AppShell. Do NOT
      // window.location.assign — that would wipe the in-memory JWT.
      await login(username, password);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-paper p-6">
      <form
        onSubmit={onSubmit}
        className="bg-card hairline p-6 w-full max-w-sm space-y-4"
      >
        <div>
          <h1 className="font-display text-xl text-ink leading-none">AI Data Anonymization</h1>
          <p className="text-xs text-mute font-mono mt-1">local sign-in</p>
        </div>
        <label className="block text-[11px] font-mono">
          <span className="text-mute">username</span>
          <input
            className="input mt-1"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
          />
        </label>
        <label className="block text-[11px] font-mono">
          <span className="text-mute">password</span>
          <input
            className="input mt-1"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {error ? (
          <div className="hairline p-2 text-[11px] font-mono text-flag">
            {error}
          </div>
        ) : null}
        <button type="submit" className="btn-accent w-full" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="text-[10px] font-mono text-mute">
          bootstrap admin: <code className="text-ink">root</code> · token is kept in memory only
        </p>
      </form>
    </div>
  );
}
