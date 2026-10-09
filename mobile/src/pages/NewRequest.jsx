import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { L } from "../labels";

const MAX_MATCHES = 8;
const UNDO_MS = 5000;
// Bought fresh daily off-system; the server refuses them too.
const EXCLUDED = new Set(["coriander", "mint"]);

// Staff send the item name only, in one tap. The owner sets the quantity on
// the admin Buy screen. Once an item is requested it disappears from this
// list for everyone until it's bought or declined.
export default function NewRequest({ onDone }) {
  const [ingredients, setIngredients] = useState([]);
  const [open, setOpen] = useState({ requested: [], running_out: [] });
  const [q, setQ] = useState("");
  const [toast, setToast] = useState(null); // { id, name }
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef(null);

  async function loadOpen() {
    try { setOpen(await api.openRequests()); } catch { /* list still works */ }
  }

  useEffect(() => {
    api.listIngredients().then(setIngredients).catch(() => {});
    loadOpen();
    return () => clearTimeout(timer.current);
  }, []);

  const taken = new Map(open.requested.map((r) => [r.item_name.toLowerCase(), r]));
  open.requested.forEach((r) => {
    const ing = r.ingredient_id && ingredients.find((i) => i.id === r.ingredient_id);
    if (ing) taken.set(ing.name.toLowerCase(), r);
  });
  const isTaken = (name) => taken.has(name.toLowerCase());

  const query = q.trim().toLowerCase();
  const available = ingredients.filter((i) => !isTaken(i.name) && !EXCLUDED.has(i.name.toLowerCase()));
  const matches = (query ? available.filter((i) => i.name.toLowerCase().includes(query)) : available).slice(0, query ? MAX_MATCHES : 6);
  const alreadyMatches = query ? [...taken.entries()].filter(([n]) => n.includes(query)).map(([, r]) => r) : [];
  const exact = ingredients.some((i) => i.name.toLowerCase() === query);
  const canSendTyped = query && !exact && !matches.length && !alreadyMatches.length && !EXCLUDED.has(query);
  const chips = open.running_out.filter((c) => !isTaken(c.name));

  async function send(name) {
    if (busy || !name || isTaken(name)) return;
    setBusy(true);
    setError(null);
    try {
      const req = await api.createRequisition({ item_name: name });
      setQ("");
      setToast({ id: req.id, name: req.item_name });
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setToast(null), UNDO_MS);
      await loadOpen();
    } catch (err) {
      setError(err.message);
      await loadOpen();
    } finally {
      setBusy(false);
    }
  }

  async function undo() {
    if (!toast) return;
    clearTimeout(timer.current);
    const { id } = toast;
    setToast(null);
    try { await api.withdrawRequisition(id); } catch (err) { setError(err.message); }
    await loadOpen();
  }

  return (
    <div className="nr">
      <div className="nr-head">
        <button type="button" className="nr-back" onClick={onDone} aria-label="Back">←</button>
        <h3>{L.requestItem}</h3>
      </div>

      <div className="nr-search">
        <span>⌕</span>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={L.typeItemName} autoComplete="off" />
      </div>
      <p className="nr-hint">{L.nameOnlyHint}</p>
      {error && <div className="error-text">{error}</div>}

      {!query && chips.length > 0 && (
        <>
          <h4 className="nr-h">{L.runningOutTap}</h4>
          <div className="nr-chips">
            {chips.map((c) => (
              <button key={c.ingredient_id} className="nr-chip" disabled={busy} onClick={() => send(c.name)}>
                {c.name}<small>{c.out ? L.out : `${c.days_left} ${L.daysLeft}`}</small>
              </button>
            ))}
          </div>
        </>
      )}

      <h4 className="nr-h">{query ? L.matches : L.allItems}</h4>
      <div className="nr-list">
        {matches.map((i) => (
          <button key={i.id} className="nr-it" disabled={busy} onClick={() => send(i.name)}>
            <span><b>{i.name}</b><small>{i.category}</small></span><span className="nr-go">{L.send}</span>
          </button>
        ))}
        {alreadyMatches.map((r) => (
          <div key={`t${r.item_name}`} className="nr-it nr-taken">
            <span><b>{r.item_name}</b><small>{L.alreadyRequested} · {r.by}</small></span><span className="badge approved">{L.statusSent}</span>
          </div>
        ))}
        {canSendTyped && (
          <button className="nr-it nr-typed" disabled={busy} onClick={() => send(q.trim())}>
            <span><b>"{q.trim()}"</b><small>{L.sendAsTyped}</small></span><span className="nr-go">{L.send}</span>
          </button>
        )}
      </div>

      {toast && (
        <div className="nr-toast">
          ✓ <span><b>{toast.name}</b> {L.sentToOwner}</span>
          <button type="button" onClick={undo}>{L.undo}</button>
        </div>
      )}
    </div>
  );
}
