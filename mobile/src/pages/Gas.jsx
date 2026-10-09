import { useEffect, useState } from "react";
import { api } from "../api";
import { L } from "../labels";
import { scheduleGasReminders } from "../reminders";

const NAME = { tandoor: L.tandoor, kitchen: L.kitchen };
const fmt = (n) => (Math.round(n * 10) / 10).toFixed(1);
const when = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });

// Both cylinders weighed every night at 12:30 AM. Staff type the scale
// reading (cylinder included); the 20 kg empty weight is subtracted here and
// on the server. A much heavier reading must be confirmed as a new cylinder.
export default function Gas() {
  const [data, setData] = useState(null);
  const [vals, setVals] = useState({});
  const [isNew, setIsNew] = useState({});
  const [editing, setEditing] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      const d = await api.gasTonight();
      setData(d);
      scheduleGasReminders({ doneTonight: d.done, hour: d.reminder.hour, minute: d.reminder.minute });
    } catch (err) {
      setError(err.message);
    }
  }
  useEffect(() => { load(); }, []);

  if (error && !data) return <div className="error-text">{error}</div>;
  if (!data) return <div className="empty-state">{L.loading}</div>;
  const tare = data.tare_kg;

  function check(c) {
    const v = parseFloat(vals[c.key]);
    if (!v) return null;
    if (v < tare - 0.5) return { warn: L.lighterThanEmpty };
    const prev = c.last_stale ? null : c.last_gross;
    if (!isNew[c.key] && prev != null && v > prev + 1) return { warn: `${fmt(v - prev)} ${L.heavier}` };
    if (isNew[c.key]) return { ok: `${L.newHas} ${fmt(v - tare)} kg ${L.gasLeft}` };
    if (prev != null) return { ok: `${L.usedSince} ${fmt(Math.max(prev - v, 0))} kg · ${fmt(v - tare)} kg ${L.gasLeft}` };
    return { ok: `${fmt(v - tare)} kg ${L.gasLeft}` };
  }

  async function save() {
    const todo = editing ? data.cylinders : data.cylinders.filter((c) => !c.logged);
    if (todo.some((c) => !parseFloat(vals[c.key]))) { setError(L.weighBoth); return; }
    if (todo.some((c) => check(c)?.warn)) { setError(todo.map((c) => check(c)?.warn).filter(Boolean)[0]); return; }
    setSaving(true);
    setError(null);
    try {
      const res = await api.saveGas(todo.map((c) => ({ cylinder: c.key, gross_kg: parseFloat(vals[c.key]), is_new: !!isNew[c.key] })));
      setResult(res);
      setData(res.state);
      setEditing(false);
      setVals({});
      setIsNew({});
      scheduleGasReminders({ doneTonight: res.state.done, hour: res.state.reminder.hour, minute: res.state.reminder.minute });
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  if (data.done && !editing) {
    const byKey = Object.fromEntries((result?.cylinders || []).map((c) => [c.key, c]));
    return (
      <div className="gas">
        <div className="gas-done">
          <div className="gas-tick">✓</div>
          <h3>{L.gasDone}</h3>
          <div className="gas-sum">
            {data.cylinders.map((c) => (
              <div key={c.key}>
                <span>{NAME[c.key]}</span>
                <b>{fmt(c.logged.gross_kg)} kg{byKey[c.key]?.used != null ? ` · ${L.usedSince} ${fmt(byKey[c.key].used)} kg` : ""}</b>
              </div>
            ))}
          </div>
          {(result?.low || []).map((k) => (
            <div key={k} className="gas-alert"><b>{NAME[k]}</b> {L.almostEmpty}</div>
          ))}
          <p className="muted" style={{ marginTop: 10 }}>{data.cylinders[0].logged.by} · {new Date(data.cylinders[0].logged.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</p>
          <button className="secondary" style={{ marginTop: 12 }} onClick={() => {
            setEditing(true);
            setVals(Object.fromEntries(data.cylinders.map((c) => [c.key, String(c.logged.gross_kg)])));
            setIsNew(Object.fromEntries(data.cylinders.map((c) => [c.key, c.logged.is_new])));
          }}>{L.edit}</button>
        </div>
      </div>
    );
  }

  return (
    <div className="gas">
      <h3 style={{ marginTop: 0 }}>{L.gasTitle}</h3>
      {data.cylinders.map((c) => {
        const r = check(c);
        const done = c.logged && !editing;
        return (
          <div key={c.key} className={`gas-card gas-${c.key}${done ? " gas-saved" : ""}`}>
            <div className="gas-nm">{NAME[c.key]}{done && <span className="gas-ok">✓ {fmt(c.logged.gross_kg)} kg</span>}</div>
            {!done && (
              <>
                <div className="gas-last">
                  {c.last_gross != null
                    ? <>{c.last_stale ? `${L.lastReading} ${when(c.last_at)}` : L.lastNight}: <b>{fmt(c.last_gross)} kg</b> {L.onTheScale}</>
                    : " "}
                </div>
                <div className="gas-entry">
                  <input inputMode="decimal" placeholder="kg" value={vals[c.key] || ""}
                    onChange={(e) => setVals({ ...vals, [c.key]: e.target.value })} />
                  <span>kg</span>
                </div>
                <label className="gas-sw">
                  <input type="checkbox" checked={!!isNew[c.key]} onChange={(e) => setIsNew({ ...isNew, [c.key]: e.target.checked })} />
                  {L.newCylinder}
                </label>
                {r?.warn && <div className="gas-warn">{r.warn}</div>}
                {r?.ok && <div className="gas-res">{r.ok}</div>}
              </>
            )}
          </div>
        );
      })}
      {error && <div className="error-text">{error}</div>}
      <button className="primary gas-save" disabled={saving} onClick={save}>
        {saving ? "..." : (data.cylinders.filter((c) => !c.logged).length > 1 || editing ? L.saveBoth : L.saveGas)}
      </button>
      <p className="muted" style={{ textAlign: "center", marginTop: 8, fontSize: 12 }}>{L.weighHint}</p>
    </div>
  );
}
