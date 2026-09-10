import { useState } from "react";
import Login from "./pages/Login";
import Requisitions from "./pages/Requisitions";
import LowStock from "./pages/LowStock";
import { getSession, clearSession } from "./api";
import { L } from "./labels";

export default function App() {
  const [session, setSession] = useState(getSession());
  const [tab, setTab] = useState("requests");

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
        {tab === "requests" && <Requisitions session={session} />}
        {tab === "stock" && <LowStock />}
      </div>

      <div className="tabbar">
        <button className={tab === "requests" ? "active" : ""} onClick={() => setTab("requests")}>
          <span className="icon">📋</span>
          {L.requestsTab}
        </button>
        <button className={tab === "stock" ? "active" : ""} onClick={() => setTab("stock")}>
          <span className="icon">📦</span>
          {L.stockTab}
        </button>
      </div>
    </div>
  );
}
