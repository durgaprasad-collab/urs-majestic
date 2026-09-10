import { useState } from "react";
import { api, saveSession } from "../api";
import { L } from "../labels";

export default function Login({ onLoggedIn }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const session = await api.login(username.trim(), password);
      saveSession(session);
      onLoggedIn(session);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-screen">
      <h1>URS Majestic</h1>
      <div className="subtitle">{L.staffApp}</div>
      <form onSubmit={submit}>
        <label>{L.username}</label>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoCapitalize="none"
          required
        />
        <label>{L.pin}</label>
        <input
          type="password"
          inputMode="numeric"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {error && <div className="error-text">{error}</div>}
        <div style={{ marginTop: 20 }}>
          <button className="primary" disabled={loading} type="submit">
            {loading ? "..." : L.login}
          </button>
        </div>
      </form>
    </div>
  );
}
