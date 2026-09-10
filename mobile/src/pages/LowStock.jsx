import { useEffect, useState } from "react";
import { api } from "../api";
import { L } from "../labels";

function StockCard({ item, onSaved }) {
  const [qty, setQty] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(false);

  async function save() {
    if (qty === "") return;
    setBusy(true);
    setError(null);
    try {
      await api.submitStockCount({ ingredient_id: item.ingredient_id, qty: Number(qty), unit: item.unit });
      onSaved("count");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function requestNow() {
    setBusy(true);
    try {
      await api.createRequisition({
        item_name: item.name,
        quantity: null,
        unit: null,
        urgency: "urgent",
        note: "Auto-suggested from low stock",
      });
      onSaved("request");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h3>{item.name}</h3>
      <div className="muted">{item.category || "Other"}</div>
      <div className="cover-days" style={{ marginTop: 6 }}>
        {item.cover_days != null
          ? `${item.cover_days.toFixed(1)} ${L.daysLeft}`
          : L.noCountYet}
      </div>

      {!editing ? (
        <div className="btn-row">
          <button className="secondary" onClick={() => setEditing(true)}>
            {L.updateCount}
          </button>
          <button className="approve" onClick={requestNow} disabled={busy}>
            {L.requestBtn}
          </button>
        </div>
      ) : (
        <div style={{ marginTop: 10 }}>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              type="number" min="0" step="0.01" autoFocus
              placeholder={`Qty in ${item.unit}`}
              value={qty} onChange={(e) => setQty(e.target.value)}
              style={{ flex: 2 }}
            />
            <span style={{ alignSelf: "center", fontWeight: 600 }}>{item.unit}</span>
          </div>
          {error && <div className="error-text">{error}</div>}
          <div className="btn-row">
            <button className="secondary" onClick={() => setEditing(false)}>{L.cancel}</button>
            <button className="approve" disabled={busy} onClick={save}>{L.save}</button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function LowStock({ onRequested }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);

  async function load() {
    try {
      setRows(await api.lowStock());
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleSaved(kind) {
    if (kind === "request") {
      // Jump straight to Requests so the staff member sees it landed --
      // no visible confirmation here was why people were tapping it
      // repeatedly, each tap creating a duplicate request.
      onRequested?.();
      return;
    }
    await load();
    setToast(L.savedToast);
    setTimeout(() => setToast(null), 2000);
  }

  return (
    <div>
      <h3 style={{ marginTop: 0 }}>{L.under3Days}</h3>
      {error && <div className="error-text">{error}</div>}
      {rows === null && <div className="empty-state">{L.loading}</div>}
      {rows && rows.length === 0 && (
        <div className="empty-state">{L.nothingLow}</div>
      )}
      {rows && rows.map((item) => (
        <StockCard key={item.ingredient_id} item={item} onSaved={handleSaved} />
      ))}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
