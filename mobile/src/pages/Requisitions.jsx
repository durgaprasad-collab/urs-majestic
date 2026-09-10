import { useEffect, useState } from "react";
import { api } from "../api";
import { L } from "../labels";
import NewRequest from "./NewRequest";

const STATUS_LABEL = {
  pending: L.pending,
  approved: L.approved,
  rejected: L.rejected,
  fulfilled: L.fulfilled,
};

function RequisitionCard({ req, isOwner, onDecide }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  async function decide(approve) {
    setBusy(true);
    try {
      await onDecide(req.id, approve, note.trim() || null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h3>
        {req.item_name}
        {req.quantity ? ` — ${req.quantity} ${req.unit}` : ""}
      </h3>
      <div className="muted">
        {L.by} {req.requested_by.name} · {new Date(req.created_at).toLocaleString()}
      </div>
      <div style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
        <span className={`badge ${req.status}`}>{STATUS_LABEL[req.status]}</span>
        {req.urgency === "urgent" && <span className="badge urgent">{L.urgent}</span>}
      </div>
      {req.note && <div className="muted" style={{ marginTop: 6 }}>"{req.note}"</div>}
      {req.decision_note && (
        <div className="muted" style={{ marginTop: 6 }}>
          {L.ownerNote}: "{req.decision_note}"
        </div>
      )}
      {req.status === "approved" && (
        <div className="muted" style={{ marginTop: 6 }}>
          {L.waitingPurchase}
        </div>
      )}

      {isOwner && req.status === "pending" && (
        <>
          <input
            placeholder={L.notePlaceholder}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            style={{ marginTop: 10 }}
          />
          <div className="btn-row">
            <button className="reject" disabled={busy} onClick={() => decide(false)}>
              {L.reject}
            </button>
            <button className="approve" disabled={busy} onClick={() => decide(true)}>
              {L.approve}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export default function Requisitions({ session }) {
  const [rows, setRows] = useState(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);

  async function load() {
    try {
      setRows(await api.listRequisitions());
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { load(); }, []);

  async function decide(id, approve, decision_note) {
    await api.decideRequisition(id, approve, decision_note);
    await load();
  }

  if (creating) {
    return <NewRequest onDone={() => { setCreating(false); load(); }} onCancel={() => setCreating(false)} />;
  }

  return (
    <div>
      {error && <div className="error-text">{error}</div>}
      {rows === null && <div className="empty-state">{L.loading}</div>}
      {rows && rows.length === 0 && (
        <div className="empty-state">{L.noRequestsYet}</div>
      )}
      {rows && rows.map((r) => (
        <RequisitionCard key={r.id} req={r} isOwner={session.is_owner} onDecide={decide} />
      ))}
      {!session.is_owner && (
        <button className="fab" onClick={() => setCreating(true)}>+</button>
      )}
    </div>
  );
}
