import { useEffect, useState } from "react";
import Login from "./pages/Login";
import Requisitions from "./pages/Requisitions";
import LowStock from "./pages/LowStock";
import Count from "./pages/Count";
import Gas from "./pages/Gas";
import { api, getSession, saveSession, clearSession } from "./api";
import { L } from "./labels";

export default function App() {
  const [session, setSession] = useState(getSession());
  // The nightly count is the main job, so the app opens on it (and the 10 PM
  // reminder lands here too). After midnight it's gas weigh-in time (12:30 AM).
  const [tab, setTab] = useState(new Date().getHours() < 5 ? "gas" : "count");

  useEffect(() => {
    if (!session) return;
    api.me().then((fresh) => {
      saveSession(fresh);
      setSession({ user_id: fresh.user_id, name: fresh.name, is_owner: fresh.is_owner });
    }).catch(() => {
      // The request helper clears expired sessions and reloads on 401. For a
      // temporary network error, retain the current session and retry next open.
    });
  }, []);

  if (!session) {
    return <Login onLoggedIn={setSession} />;
  }

  function logout() {
    clearSession();
    setSession(null);
  }

  return (
    <div className="app">
      <div className="topbar">
        <div>
          <h1>URS Majestic</h1>
          <div className="who">{session.name} · {session.is_owner ? L.owner : L.staff}</div>
        </div>
        <button className="logout-btn" onClick={logout}>{L.logout}</button>
      </div>

      <div className="content">
        {tab === "count" && <Count session={session} />}
        {tab === "gas" && <Gas />}
        {tab === "requests" && <Requisitions session={session} />}
        {tab === "stock" && <LowStock onRequested={() => setTab("requests")} />}
      </div>

      <div className="tabbar">
        <button className={tab === "count" ? "active" : ""} onClick={() => setTab("count")}>
          <span className="icon">✅</span>
          {L.countTab}
        </button>
        <button className={tab === "gas" ? "active" : ""} onClick={() => setTab("gas")}>
          <span className="icon">🔥</span>
          {L.gasTab}
        </button>
        <button className={tab === "requests" ? "active" : ""} onClick={() => setTab("requests")}>
          <span className="icon">📋</span>
          {L.requestsTab}
        </button>
        <button className={tab === "stock" ? "active" : ""} onClick={() => setTab("stock")}>
          <span className="icon">⚠️</span>
          {L.lowTab}
        </button>
      </div>
    </div>
  );
}
