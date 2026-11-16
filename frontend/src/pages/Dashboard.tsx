import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, Company, Report, User } from "../api";

const KIND: Record<string, string> = { zakup: "Zakup", sprzedaz: "Sprzedaż", oba: "Zakup + sprzedaż" };
const STATUS: Record<string, string> = { queued: "W kolejce", running: "W trakcie", done: "Gotowy", error: "Błąd" };

function iso(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function prevMonth(): [string, string] {
  const now = new Date();
  return [iso(new Date(now.getFullYear(), now.getMonth() - 1, 1)), iso(new Date(now.getFullYear(), now.getMonth(), 0))];
}

export default function Dashboard({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [company, setCompany] = useState<Company | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    api.get<Company | null>("/api/company").then((c) => { setCompany(c); setLoaded(true); });
  }, []);

  async function logout() {
    await api.post("/api/auth/logout");
    onLogout();
  }

  return (
    <div className="page">
      <header>
        <strong>KSeF – zestawienia</strong>
        <span className="muted">{user.email}</span>
        <button onClick={logout}>Wyloguj</button>
      </header>
      <div className="banner">Wersja testowa</div>
      {loaded && <CompanyCard company={company} onChange={setCompany} />}
      {loaded && <Reports ready={!!company?.has_credentials} />}
    </div>
  );
}

function CompanyCard({ company, onChange }: { company: Company | null; onChange: (c: Company) => void }) {
  const [nip, setNip] = useState(company?.nip ?? "");
  const [name, setName] = useState(company?.name ?? "");
  const [env, setEnv] = useState(company?.ksef_env ?? "prod");
  const [mode, setMode] = useState<"pem" | "p12">("pem");
  const [certFile, setCertFile] = useState<File | null>(null);
  const [keyFile, setKeyFile] = useState<File | null>(null);
  const [p12File, setP12File] = useState<File | null>(null);
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setMsg(null);
    try { await fn(); } catch (e) { setMsg({ ok: false, text: (e as Error).message }); } finally { setBusy(false); }
  }

  const saveCompany = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      const c = await api.put<Company>("/api/company", { nip, name, ksef_env: env });
      onChange(c);
      setNip(c.nip); setName(c.name); setEnv(c.ksef_env);
      setMsg({ ok: true, text: "Zapisano dane firmy." });
    });
  };

  const uploadCert = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      const fd = new FormData();
      if (mode === "p12") { if (p12File) fd.append("p12", p12File); }
      else { if (certFile) fd.append("cert", certFile); if (keyFile) fd.append("key", keyFile); }
      fd.append("password", password);
      onChange(await api.post<Company>("/api/company/certificate", fd));
      setPassword("");
      setMsg({ ok: true, text: "Certyfikat zapisany (zaszyfrowany)." });
    });
  };

  const testConn = () => run(async () => {
    const r = await api.post<{ message: string }>("/api/company/test");
    setMsg({ ok: true, text: r.message });
  });

  const setMethod = (m: string) => run(async () => {
    onChange(await api.put<Company>("/api/company/auth-method", { auth_method: m }));
  });

  const saveToken = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      onChange(await api.post<Company>("/api/company/token", { token }));
      setToken("");
      setMsg({ ok: true, text: "Token zapisany (zaszyfrowany)." });
    });
  };

  const removeToken = () => run(async () => {
    if (!confirm("Usunąć token KSeF z aplikacji?")) return;
    onChange(await api.del<Company>("/api/company/token"));
  });

  const removeCert = () => run(async () => {
    if (!confirm("Usunąć certyfikat KSeF z aplikacji?")) return;
    onChange(await api.del<Company>("/api/company/certificate"));
  });

  const dirty = !company || nip !== company.nip || name !== company.name || env !== company.ksef_env;

  const validTo = company?.cert_valid_to ? new Date(company.cert_valid_to) : null;
  const daysLeft = validTo ? Math.floor((validTo.getTime() - Date.now()) / 86400000) : null;

  return (
    <section className="card">
      <h2>1. Firma i dostęp do KSeF</h2>
      <form onSubmit={saveCompany} className="row">
        <label>NIP<input value={nip} onChange={(e) => setNip(e.target.value)} required placeholder="10 cyfr" /></label>
        <label className="grow">Nazwa (opcjonalnie)<input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label>Środowisko
          <select value={env} onChange={(e) => setEnv(e.target.value)}>
            <option value="prod">Produkcja</option>
            <option value="test">Test</option>
            <option value="demo">Demo</option>
          </select>
        </label>
        <button className="primary" disabled={busy}>Zapisz</button>
      </form>
      {company && dirty && (
        <div className="error">Niezapisane zmiany – kliknij „Zapisz”. Do tego czasu aplikacja używa:
          NIP {company.nip}, środowisko {company.ksef_env === "prod" ? "Produkcja" : company.ksef_env === "test" ? "Test" : "Demo"}.</div>
      )}
      {company && company.ksef_env !== "prod" && !dirty && (
        <div className="warn small">Uwaga: zapisane środowisko to {company.ksef_env === "test" ? "Test" : "Demo"} – prawdziwe faktury i tokeny są tylko w środowisku Produkcja.</div>
      )}

      {company && (
        <>
          <h3>Logowanie do KSeF</h3>
          <div className="tabs small">
            <button type="button" className={company.auth_method === "cert" ? "active" : ""} onClick={() => setMethod("cert")} disabled={busy}>Certyfikat</button>
            <button type="button" className={company.auth_method === "token" ? "active" : ""} onClick={() => setMethod("token")} disabled={busy}>Token</button>
          </div>

          {company.auth_method === "token" ? (
            <>
              {company.has_token ? (
                <div className="certinfo">
                  <div><span className="muted">Token:</span> zapisany (zaszyfrowany)</div>
                </div>
              ) : (
                <p className="muted">Brak tokena. Wygeneruj go w Aplikacji Podatnika KSeF (uprawnienie: przeglądanie faktur).</p>
              )}
              <p className="warn small">Tokeny KSeF działają do 31.12.2026 – od 2027 r. potrzebny będzie certyfikat.</p>
              <form onSubmit={saveToken} className="row">
                <label className="grow">Token KSeF
                  <input type="password" value={token} onChange={(e) => setToken(e.target.value)} required autoComplete="off" placeholder="wklej token" />
                </label>
                <button className="primary" disabled={busy}>{company.has_token ? "Zastąp token" : "Zapisz token"}</button>
              </form>
              {company.has_token && (
                <div className="row">
                  <button onClick={testConn} disabled={busy}>Testuj połączenie z KSeF</button>
                  <button onClick={removeToken} disabled={busy} className="danger">Usuń token</button>
                </div>
              )}
            </>
          ) : (
            <>
              {company.has_certificate ? (
                <div className="certinfo">
                  <div><span className="muted">Podmiot:</span> {company.cert_subject}</div>
                  <div>
                    <span className="muted">Ważny do:</span> {validTo?.toLocaleDateString("pl-PL")}
                    {daysLeft !== null && daysLeft < 30 && <span className="warn"> – wygasa za {daysLeft} dni</span>}
                  </div>
                  <div className="row">
                    <button onClick={testConn} disabled={busy}>Testuj połączenie z KSeF</button>
                    <button onClick={removeCert} disabled={busy} className="danger">Usuń certyfikat</button>
                  </div>
                </div>
              ) : (
                <p className="muted">Brak certyfikatu. Pobierz go w Module Certyfikatów i Uprawnień KSeF
                  (przeznaczenie: „Uwierzytelnienie w systemie KSeF”).</p>
              )}

              <form onSubmit={uploadCert}>
                <div className="tabs small">
                  <button type="button" className={mode === "pem" ? "active" : ""} onClick={() => setMode("pem")}>.crt + .key</button>
                  <button type="button" className={mode === "p12" ? "active" : ""} onClick={() => setMode("p12")}>.p12 / .pfx</button>
                </div>
                {mode === "pem" ? (
                  <div className="row">
                    <label>Certyfikat (.crt)<input type="file" accept=".crt,.pem,.cer" onChange={(e) => setCertFile(e.target.files?.[0] ?? null)} required /></label>
                    <label>Klucz prywatny (.key)<input type="file" accept=".key,.pem" onChange={(e) => setKeyFile(e.target.files?.[0] ?? null)} required /></label>
                  </div>
                ) : (
                  <label>Plik .p12 / .pfx<input type="file" accept=".p12,.pfx" onChange={(e) => setP12File(e.target.files?.[0] ?? null)} required /></label>
                )}
                <div className="row">
                  <label className="grow">Hasło do klucza<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="off" /></label>
                  <button className="primary" disabled={busy}>{company.has_certificate ? "Zastąp certyfikat" : "Wgraj certyfikat"}</button>
                </div>
              </form>
            </>
          )}
        </>
      )}
      {msg && <div className={msg.ok ? "ok" : "error"}>{msg.text}</div>}
    </section>
  );
}

function Reports({ ready }: { ready: boolean }) {
  const [[from, to], setRange] = useState<[string, string]>(prevMonth());
  const [kind, setKind] = useState("oba");
  const [dateType, setDateType] = useState("wystawienia");
  const [reports, setReports] = useState<Report[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(() => api.get<Report[]>("/api/reports").then(setReports).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);

  // odświeżanie, dopóki jakiś raport jest w toku
  const active = reports.some((r) => r.status === "queued" || r.status === "running");
  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [active, load]);

  async function create(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api.post("/api/reports", { date_from: from, date_to: to, kind, date_type: dateType });
      load();
    } catch (err) { setError((err as Error).message); }
  }

  async function remove(id: number) {
    try { await api.del(`/api/reports/${id}`); load(); } catch (err) { setError((err as Error).message); }
  }

  const today = iso(new Date());
  return (
    <section className="card">
      <h2>2. Raport dla księgowej</h2>
      {!ready && <p className="muted">Najpierw zapisz NIP oraz certyfikat lub token KSeF.</p>}
      <form onSubmit={create} className="row">
        <label>Od<input type="date" value={from} max={to} onChange={(e) => setRange([e.target.value, to])} required /></label>
        <label>Do<input type="date" value={to} min={from} max={today} onChange={(e) => setRange([from, e.target.value])} required /></label>
        <label>Faktury
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="oba">Zakup + sprzedaż</option>
            <option value="zakup">Zakup</option>
            <option value="sprzedaz">Sprzedaż</option>
          </select>
        </label>
        <label>Wg daty
          <select value={dateType} onChange={(e) => setDateType(e.target.value)}>
            <option value="wystawienia">wystawienia</option>
            <option value="ksef">zapisu w KSeF</option>
          </select>
        </label>
        <button className="primary" disabled={!ready || active}>Generuj raport</button>
      </form>
      <div className="quick">
        <button type="button" onClick={() => setRange(prevMonth())}>Poprzedni miesiąc</button>
        <button type="button" onClick={() => { const n = new Date(); setRange([iso(new Date(n.getFullYear(), n.getMonth(), 1)), today]); }}>Bieżący miesiąc</button>
      </div>
      {error && <div className="error">{error}</div>}
      <p className="muted small">Duże zakresy mogą trwać długo – KSeF ogranicza liczbę pobrań faktur na godzinę.
        Raport generuje się w tle, możesz zamknąć stronę.</p>

      <table>
        <thead>
          <tr><th>Okres</th><th>Rodzaj</th><th>Status</th><th>Faktur</th><th></th></tr>
        </thead>
        <tbody>
          {reports.length === 0 && <tr><td colSpan={5} className="muted">Brak raportów</td></tr>}
          {reports.map((r) => (
            <tr key={r.id}>
              <td>{r.date_from} – {r.date_to}</td>
              <td>{KIND[r.kind]}<div className="muted small">wg daty {r.date_type === "ksef" ? "KSeF" : "wystawienia"}</div></td>
              <td>
                <span className={`badge ${r.status}`}>{STATUS[r.status]}</span>
                {r.status !== "done" && <div className="muted small">{r.progress}</div>}
              </td>
              <td>{r.status === "done" ? `${r.invoices}${r.errors ? ` (błędy: ${r.errors})` : ""}` : "–"}</td>
              <td className="actions">
                {r.downloadable && <a className="button primary" href={`/api/reports/${r.id}/download`}>Pobierz .xlsx</a>}
                {r.status !== "running" && <button onClick={() => remove(r.id)}>Usuń</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
