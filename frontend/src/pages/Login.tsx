import { FormEvent, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, User } from "../api";

const GOOGLE_ERRORS: Record<string, string> = {
  google: "Logowanie przez Google nie powiodło się.",
  google_email: "Konto Google nie ma zweryfikowanego adresu e-mail.",
};

export default function Login({ onLogin }: { onLogin: (u: User) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [google, setGoogle] = useState(false);
  const [params] = useSearchParams();

  useEffect(() => {
    api.get<{ google: boolean }>("/api/auth/config").then((c) => setGoogle(c.google)).catch(() => {});
    const e = params.get("error");
    if (e) setError(GOOGLE_ERRORS[e] ?? "Błąd logowania.");
  }, [params]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (mode === "register" && password !== password2) {
      setError("Hasła nie są takie same.");
      return;
    }
    setBusy(true);
    try {
      const u = await api.post<User>(`/api/auth/${mode}`, { email, password });
      onLogin(u);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center">
      <div className="card narrow">
        <h1>KSeF – zestawienia</h1>
        <p className="muted">Raporty faktur z KSeF dla księgowej</p>

        {google && (
          <>
            {/* pełne przekierowanie przeglądarki - przepływ OAuth po stronie backendu */}
            <a className="button google" href="/api/auth/google/login">
              <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
                <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.1C12.5 13.6 17.8 9.5 24 9.5z"/>
                <path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.7c4.3-4 6.9-9.9 6.9-17.1z"/>
                <path fill="#FBBC05" d="M10.6 28.6c-.5-1.4-.8-3-.8-4.6s.3-3.2.8-4.6l-7.9-6.1C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.9-6.1z"/>
                <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.4-5.7c-2.1 1.4-4.8 2.3-8.5 2.3-6.2 0-11.5-4.1-13.4-9.9l-7.9 6.1C6.6 42.6 14.6 48 24 48z"/>
              </svg>
              <span>Zaloguj się kontem Google</span>
            </a>
            <p className="muted small center-text">Bez rejestracji – konto utworzy się automatycznie.</p>
            <div className="sep">lub e-mail i hasło</div>
          </>
        )}

        <div className="tabs">
          <button className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>Logowanie</button>
          <button className={mode === "register" ? "active" : ""} onClick={() => setMode("register")}>Rejestracja</button>
        </div>

        <form onSubmit={submit}>
          <label>E-mail
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
          </label>
          <label>Hasło
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
              minLength={mode === "register" ? 10 : undefined}
              autoComplete={mode === "login" ? "current-password" : "new-password"} />
          </label>
          {mode === "register" && (
            <label>Powtórz hasło
              <input type="password" value={password2} onChange={(e) => setPassword2(e.target.value)} required autoComplete="new-password" />
            </label>
          )}
          {error && <div className="error">{error}</div>}
          <button className="primary" disabled={busy}>
            {busy ? "Proszę czekać…" : mode === "login" ? "Zaloguj" : "Utwórz konto"}
          </button>
        </form>

      </div>
    </div>
  );
}
