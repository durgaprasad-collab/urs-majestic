import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { L } from "../labels";
import { scheduleCountReminders } from "../reminders";

// Tonight's stock count. Each number saves the moment the box loses focus or
// "Next" is pressed -- there is no Save button to forget. Owners also get a
// ⋯ menu per item to disable things no longer in use.
function fmt(q) {
  if (q == null) return "";
  return String(Math.round(q * 1000) / 1000);
}

function ItemRow({ item, saved, onSave, inputRef, onNext, isOwner, onMenu }) {
  const [value, setValue] = useState(saved != null ? fmt(saved) : "");
  const [state, setState] = useState(saved != null ? "saved" : "idle"); // idle | saving | saved | error
  const [error, setError] = useState(null);
  // Enter saves and moves focus on, which also blurs this box -- remember what
  // was last sent (or is in flight) so the blur doesn't post the same count twice.
  const sentRef = useRef(saved != null ? fmt(saved) : null);

  async function commit(v = value) {
    const raw = String(v).trim();
    if (raw === "" || raw === sentRef.current) return;
    const qty = Number(raw);
    if (Number.isNaN(qty) || qty < 0) { setState("error"); setError(L.badNumber); return; }
    sentRef.current = raw;
    setState("saving");
    try {
      await onSave(item, qty);
      setState("saved");
      setError(null);
    } catch (err) {
      sentRef.current = null; // allow a retry
      setState("error");
      setError(err.message);
    }
  }

  return (
    <div className={`count-item ${state === "saved" ? "done" : ""}`}>
      <div className="count-item-head">
        <span className="count-name">{item.name}</span>
        {state === "saved" && <span className="count-ok">✓ {fmt(Number(value))} {item.unit}</span>}
        {isOwner && <button className="count-more" onClick={() => onMenu(item)} aria-label={`Owner options for ${item.name}`}>⋯</button>}
      </div>
      <div className="count-exp">
        {item.expected != null ? `${L.expected} ${fmt(item.expected)} ${item.unit}` : L.neverCounted}
        {item.last_at && ` · ${L.lastCount} ${fmt(item.last_qty)} ${item.unit} (${item.last_at})`}
      </div>
      <div className="count-inrow">
        {item.expected != null && (
          <button className="count-same" onClick={() => { setValue(fmt(item.expected)); commit(fmt(item.expected)); }}>
            {L.same}
          </button>
        )}
        <input
          ref={inputRef}
          className={`count-num ${state}`}
          type="text"
          inputMode="decimal"
          enterKeyHint="next"
          placeholder={item.expected != null ? fmt(item.expected) : "0"}
          value={value}
          onChange={(e) => { setValue(e.target.value); if (state !== "saving") setState("idle"); }}
          onBlur={() => commit()}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); onNext(); } }}
        />
        <span className="count-unit">{item.unit}</span>
      </div>
      {error && <div className="error-text">{error}</div>}
    </div>
  );
}

export default function Count({ session }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [mode, setMode] = useState("home"); // home | counting
  const [saved, setSaved] = useState({}); // id -> qty saved tonight in this session
  const [menuFor, setMenuFor] = useState(null);
  const [toast, setToast] = useState(null);
  const refs = useRef({});
  const isOwner = !!session?.is_owner;

  async function load() {
    try {
      const d = await api.tonightCount();
      setData(d);
      scheduleCountReminders({ doneTonight: d.done >= d.total && d.total > 0, hour: d.reminder_hour, total: d.total });
    } catch (err) {
      setError(err.message);
    }
  }
  useEffect(() => { load(); }, []);

  const items = data?.items || [];
  const doneIds = useMemo(() => new Set([
    ...items.filter((i) => i.counted_tonight).map((i) => i.id),
    ...Object.keys(saved).map(Number),
  ]), [items, saved]);
  const done = doneIds.size;
  const total = items.length;
  const finished = total > 0 && done >= total;

  useEffect(() => {
    if (finished) scheduleCountReminders({ doneTonight: true, hour: data?.reminder_hour, total });
  }, [finished]);

  async function saveOne(item, qty) {
    await api.submitStockCount({ ingredient_id: item.id, qty, unit: item.unit });
    setSaved((s) => ({ ...s, [item.id]: qty }));
  }

  function focusNext(id) {
    const order = items.map((i) => i.id);
    const next = order[order.indexOf(id) + 1];
    if (next != null && refs.current[next]) refs.current[next].focus();
    else document.activeElement?.blur();
  }

  async function disable(item) {
    const reason = window.prompt(`${L.disableConfirm} "${item.name}"?\n${L.disableReasonPrompt}`, L.noLongerUsed);
    if (reason == null) return;
    try {
      await api.disableItem(item.id, reason);
      setMenuFor(null);
      setToast(`${item.name}: ${L.disabled}`);
      setTimeout(() => setToast(null), 2200);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  if (error && !data) return <div className="error-text">{error}</div>;
  if (!data) return <div className="empty-state">{L.loading}</div>;

  if (mode === "home" || finished) {
    return (
      <div>
        {finished ? (
          <div className="count-done">
            <div className="count-tick">✓</div>
            <h2>{L.doneTonight}</h2>
            <p className="muted">{total} {L.itemsCounted}. {L.noReminderUntil}</p>
            {data.running_low.length > 0 && (
              <div className="card" style={{ textAlign: "left", marginTop: 16 }}>
                <h3>{L.runningOut}</h3>
                {data.running_low.map((r) => (
                  <div className="count-low" key={r.name}><span>{r.name}</span><span>{fmt(r.qty)} {r.unit}</span></div>
                ))}
              </div>
            )}
            <button className="secondary" style={{ marginTop: 12 }} onClick={() => setMode("counting")}>{L.reviewCounts}</button>
          </div>
        ) : (
          <>
            <div className="card count-hero">
              <h3>{data.kind === "full" ? L.fullCountTitle : L.tonightTitle}</h3>
              <div className="muted">{total} {L.items} · {data.kind === "full" ? L.fullCountSub : L.dailySub}</div>
              <div className="count-pbar"><i style={{ width: `${total ? (done / total) * 100 : 0}%` }} /></div>
              <div className="muted">{done} / {total} {L.doneWord} · {L.takesAbout}</div>
              <button className="count-start" onClick={() => setMode("counting")}>
                {done ? L.continueCount : L.startCount}
              </button>
            </div>
            <div className="count-meta"><span>{L.lastCountLabel}</span><b>{data.last_day ? `${data.last_day}${data.last_by ? " · " + data.last_by : ""}` : "—"}</b></div>
            <div className="count-meta"><span>{L.reminderLabel}</span><b>{L.every10pm}</b></div>
          </>
        )}
      </div>
    );
  }

  // counting
  let lastCat = null;
  return (
    <div>
      <div className="count-sticky">
        <div className="count-progress-row"><button className="count-back" onClick={() => setMode("home")}>←</button><b>{done} / {total} {L.savedWord}</b></div>
        <div className="count-pbar"><i style={{ width: `${total ? (done / total) * 100 : 0}%` }} /></div>
      </div>
      {items.map((item) => {
        const header = item.category !== lastCat ? (lastCat = item.category) : null;
        return (
          <div key={item.id}>
            {header && <div className="count-group">{header}</div>}
            <ItemRow
              item={item}
              saved={saved[item.id] ?? null}
              onSave={saveOne}
              inputRef={(el) => { refs.current[item.id] = el; }}
              onNext={() => focusNext(item.id)}
              isOwner={isOwner}
              onMenu={setMenuFor}
            />
          </div>
        );
      })}
      <p className="muted" style={{ textAlign: "center" }}>{L.autoSaveHint}</p>

      {menuFor && (
        <div className="sheet-shade" onClick={() => setMenuFor(null)}>
          <div className="sheet" onClick={(e) => e.stopPropagation()}>
            <h3>{menuFor.name}</h3>
            <div className="muted">{L.ownerOnly}</div>
            <button className="sheet-opt" onClick={() => { const id = menuFor.id; setMenuFor(null); setTimeout(() => refs.current[id]?.focus(), 50); }}>
              ✏️ {L.correctQty}
            </button>
            <button className="sheet-opt danger" onClick={() => disable(menuFor)}>⛔ {L.disableItem}</button>
            <button className="sheet-opt" onClick={() => setMenuFor(null)}>{L.cancel}</button>
          </div>
        </div>
      )}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
