import { useState, type FormEvent } from "react";
import { api } from "../api";
import type { User } from "../types";

// Sign in, or create an account. On success the server sets the session cookie
// and returns the user, which App stores to show the rest of the app.
export function AuthScreen({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const registering = mode === "register";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (registering && password.length < 8) {
      setError("Use at least 8 characters for your password.");
      return;
    }
    setBusy(true);
    try {
      const user = registering
        ? await api.register(name.trim(), email.trim(), password)
        : await api.login(email.trim(), password);
      onSignedIn(user);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function switchMode() {
    setMode(registering ? "login" : "register");
    setError(null);
  }

  return (
    <div className="auth">
      <div className="auth__intro">
        <div className="brand brand--large">
          <span className="brand__mark" />
          <span className="brand__name">LedgerWithLeisure</span>
        </div>
        <p>Your receivables, contracts and payment reminders, in one calm conversation.</p>
      </div>

      <form className="auth__card" onSubmit={submit}>
        <h1>{registering ? "Create your account" : "Welcome back"}</h1>
        <p className="auth__sub">
          {registering ? "Join Meera Packaging's workspace." : "Sign in to Meera Packaging's workspace."}
        </p>

        {registering && (
          <label>Your name
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Meera Kulkarni"
                   autoComplete="name" required maxLength={60} />
          </label>
        )}
        <label>Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                 placeholder="meera@meerapackaging.in" autoComplete="email" required />
        </label>
        <label>Password
          <span className="password">
            <input type={showPassword ? "text" : "password"} value={password}
                   onChange={(e) => setPassword(e.target.value)}
                   placeholder={registering ? "At least 8 characters" : ""}
                   autoComplete={registering ? "new-password" : "current-password"} required />
            <button type="button" className="password__toggle" onClick={() => setShowPassword((s) => !s)}
                    aria-label={showPassword ? "Hide password" : "Show password"}>
              {showPassword ? "Hide" : "Show"}
            </button>
          </span>
        </label>

        {error && <p className="auth__error">{error}</p>}

        <button className="btn btn--primary auth__submit" disabled={busy}>
          {busy ? "One moment…" : registering ? "Create account" : "Sign in"}
        </button>
        <p className="auth__switch">
          {registering ? "Already have an account?" : "New here?"}{" "}
          <button type="button" onClick={switchMode}>{registering ? "Sign in" : "Create an account"}</button>
        </p>
      </form>
    </div>
  );
}
