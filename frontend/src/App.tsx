import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api, User } from "./api";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);

  useEffect(() => {
    api.get<User>("/api/auth/me").then(setUser).catch(() => setUser(null));
  }, []);

  if (user === undefined) return <div className="center">Ładowanie…</div>;

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" /> : <Login onLogin={setUser} />} />
      <Route path="/" element={user ? <Dashboard user={user} onLogout={() => setUser(null)} /> : <Navigate to="/login" />} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  );
}
