import { useEffect, useState } from "react";
import { api } from "../api";
import { L } from "../labels";

const UNITS = ["kg", "g", "l", "ml", "pcs"];

export default function NewRequest({ onDone, onCancel }) {
  const [ingredients, setIngredients] = useState([]);
  const [itemName, setItemName] = useState("");
  const [customItem, setCustomItem] = useState(false);
  const [quantity, setQuantity] = useState("");
  const [unit, setUnit] = useState("kg");
  const [urgent, setUrgent] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.listIngredients().then(setIngredients).catch(() => {});
  }, []);

  function pickIngredient(name) {
    setItemName(name);
    const match = ingredients.find((i) => i.name === name);
    if (match) setUnit(match.unit);
  }

  async function submit(e) {
    e.preventDefault();
    setError(null);
    if (!itemName.trim()) {
      setError(L.pickOrType);
      return;
    }
    setSaving(true);
    try {
      await api.createRequisition({
        item_name: itemName.trim(),
        quantity: quantity ? Number(quantity) : null,
        unit: quantity ? unit : null,
        urgency: urgent ? "urgent" : "normal",
        note: note.trim() || null,
      });
      onDone();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h3 style={{ marginTop: 0 }}>{L.newRequest}</h3>
      <form onSubmit={submit}>
        <label>{L.item}</label>
        {!customItem ? (
          <select value={itemName} onChange={(e) => pickIngredient(e.target.value)} required>
            <option value="">{L.selectPrompt}</option>
            {ingredients.map((i) => (
              <option key={i.id} value={i.name}>{i.name}</option>
            ))}
          </select>
        ) : (
          <input value={itemName} onChange={(e) => setItemName(e.target.value)} placeholder="e.g. AC repair" />
        )}
        <button
          type="button"
          className="secondary"
          style={{ marginTop: 8 }}
          onClick={() => { setCustomItem((v) => !v); setItemName(""); }}
        >
          {customItem ? L.chooseFromList : L.notInList}
        </button>

        <label>{L.quantityOptional}</label>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            type="number" min="0" step="0.01"
            value={quantity} onChange={(e) => setQuantity(e.target.value)}
            style={{ flex: 2 }}
          />
          <select value={unit} onChange={(e) => setUnit(e.target.value)} style={{ flex: 1 }}>
            {UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
          </select>
        </div>

        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input type="checkbox" style={{ width: "auto" }} checked={urgent} onChange={(e) => setUrgent(e.target.checked)} />
          {L.urgent}
        </label>

        <label>{L.noteOptional}</label>
        <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />

        {error && <div className="error-text">{error}</div>}

        <div className="btn-row" style={{ marginTop: 16 }}>
          <button type="button" className="secondary" onClick={onCancel} style={{ flex: 1 }}>
            {L.cancel}
          </button>
          <button type="submit" className="primary" disabled={saving} style={{ flex: 2 }}>
            {saving ? "..." : L.send}
          </button>
        </div>
      </form>
    </div>
  );
}
