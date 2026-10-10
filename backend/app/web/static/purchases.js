// Purchases: new bill (many lines, unit-locked, live price check), per-piece
// unit fix, bill search.
(function () {
  const root = document.querySelector('[data-pu]');
  if (!root) return;
  const D = JSON.parse(document.getElementById('pu-data').textContent);
  const byName = new Map(D.items.map(i => [i.name.toLowerCase(), i]));
  const byId = new Map(D.items.map(i => [i.id, i]));
  const rs = n => '₹' + Math.round(n).toLocaleString('en-IN');
  const $ = s => root.querySelector(s);
  const body = $('[data-pu-lines]');

  // ── Item suggestions: our own dropdown (the browser's datalist only matches
  // the start of a name in some browsers and is unreliable on Android).
  const norm = t => t.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  const idx = D.items.map(i => ({ it: i, n: norm(i.name) }));
  function rank(q) {
    const nq = norm(q);
    if (!nq) return [];
    const toks = nq.split(' ');
    const out = [];
    for (const { it, n } of idx) {
      let sc;
      if (n === nq) sc = 0;
      else if (n.startsWith(nq)) sc = 1;
      else if ((' ' + n).includes(' ' + nq)) sc = 2;
      else if (n.includes(nq)) sc = 3;
      else if (toks.every(t => n.includes(t))) sc = 4;
      else if (n.replace(/ /g, '').includes(nq.replace(/ /g, ''))) sc = 5;
      else continue;
      out.push([sc, n.length, it]);
    }
    return out.sort((a, b) => a[0] - b[0] || a[1] - b[1]).slice(0, 8).map(x => x[2]);
  }
  const sug = document.createElement('div');
  sug.className = 'pu-sug'; sug.hidden = true; sug.setAttribute('role', 'listbox');
  document.body.appendChild(sug);
  let sugFor = null, sugList = [], sugAt = 0;
  const esc = t => t.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function hideSug() { sug.hidden = true; sugFor = null; }
  function showSug(input) {
    sugList = rank(input.value);
    if (!sugList.length || (sugList.length === 1 && sugList[0].name === input.value.trim())) { hideSug(); return; }
    sugFor = input; sugAt = 0;
    sug.innerHTML = sugList.map((it, k) => `<div class="pu-sug-i${k === 0 ? ' on' : ''}" role="option" data-k="${k}"><b>${esc(it.name)}</b><span>${it.unit}${it.usual ? ' · ' + perLabel(it.usual, it.unit) : ''}</span></div>`).join('');
    const r = input.getBoundingClientRect();
    sug.style.left = r.left + 'px'; sug.style.top = (r.bottom + 2) + 'px'; sug.style.width = Math.max(r.width, 240) + 'px';
    sug.hidden = false;
  }
  function moveSug(d) {
    sugAt = (sugAt + d + sugList.length) % sugList.length;
    sug.querySelectorAll('.pu-sug-i').forEach((el, k) => el.classList.toggle('on', k === sugAt));
  }
  function pick(it) {
    const input = sugFor, tr = input.closest('tr');
    input.value = it.name; hideSug();
    check(tr); total();
    tr.querySelector('[data-l-qty]').focus();
  }
  sug.addEventListener('mousedown', e => { e.preventDefault(); const el = e.target.closest('.pu-sug-i'); if (el) pick(sugList[+el.dataset.k]); });
  window.addEventListener('resize', hideSug);
  window.addEventListener('scroll', () => { if (sugFor) showSug(sugFor); }, true);

  // Units a line can be typed in, per the item's tracked unit; all convert back.
  function altUnits(it) {
    if (it.unit === 'kg' || it.unit === 'g') return it.pack_g ? ['kg', 'g', 'pcs'] : ['kg', 'g'];
    if (it.unit === 'l' || it.unit === 'ml') return ['l', 'ml'];
    if (it.unit === 'pcs') return it.pack_g ? ['pcs', 'kg', 'g'] : ['pcs'];
    return [it.unit];
  }
  const label = (u, it) => (u === 'pcs' && it.pack_g ? `pcs (${it.pack_g} g)` : u);
  function toItemUnit(q, u, it) {
    if (u === it.unit) return q;
    const f = { 'kg>g': 1000, 'g>kg': 0.001, 'l>ml': 1000, 'ml>l': 0.001 }[`${u}>${it.unit}`];
    if (f) return q * f;
    if (it.pack_g && it.unit === 'pcs' && (u === 'kg' || u === 'g')) return (u === 'kg' ? q * 1000 : q) / it.pack_g;
    if (it.pack_g && u === 'pcs') { const g = q * it.pack_g; return it.unit === 'kg' ? g / 1000 : g; }
    return null;
  }
  const perLabel = (p, u) => (u === 'g' ? `₹${(p * 1000).toFixed(0)}/kg` : u === 'ml' ? `₹${(p * 1000).toFixed(0)}/L`
    : `₹${p < 10 ? p.toFixed(1) : p.toFixed(0)}/${u}`);

  function addLine(focus) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><input data-l-item placeholder="Start typing an item…" autocomplete="off" aria-label="Item"></td>
      <td class="r"><input inputmode="decimal" data-l-qty class="pu-num" aria-label="Quantity"></td>
      <td><select data-l-unit aria-label="Unit" disabled><option>—</option></select></td>
      <td class="r"><input inputmode="decimal" data-l-amt class="pu-num" aria-label="Amount"></td>
      <td data-l-chk class="pu-chk"></td>
      <td><button type="button" class="pu-x" data-l-del aria-label="Remove line">×</button></td>`;
    body.appendChild(tr);
    if (focus) tr.querySelector('[data-l-item]').focus();
    return tr;
  }
  function itemOf(tr) { return byName.get(tr.querySelector('[data-l-item]').value.trim().toLowerCase()); }

  function check(tr) {
    const it = itemOf(tr), sel = tr.querySelector('[data-l-unit]'), chk = tr.querySelector('[data-l-chk]');
    if (it && sel.dataset.for !== String(it.id)) {
      sel.innerHTML = altUnits(it).map(u => `<option value="${u}">${label(u, it)}</option>`).join('');
      sel.disabled = false; sel.dataset.for = it.id;
    } else if (!it) { sel.innerHTML = '<option>—</option>'; sel.disabled = true; delete sel.dataset.for; }
    const q = parseFloat(tr.querySelector('[data-l-qty]').value), a = parseFloat(tr.querySelector('[data-l-amt]').value);
    chk.className = 'pu-chk'; chk.textContent = '';
    if (!tr.querySelector('[data-l-item]').value.trim()) return;
    if (!it) { chk.textContent = 'Not in the item list'; chk.classList.add('bad'); return; }
    if (!(q > 0) || !(a >= 0)) return;
    const qi = toItemUnit(q, sel.value, it);
    let pre = sel.value !== it.unit ? `= ${+qi.toFixed(3)} ${it.unit} · ` : '';
    const p = a / qi;
    if (!it.usual) { chk.textContent = pre + perLabel(p, it.unit) + ' · no price history'; return; }
    const dev = p / it.usual - 1;
    chk.textContent = pre + perLabel(p, it.unit) + (Math.abs(dev) < 0.15 ? ' · usual'
      : ` · ${dev > 0 ? '+' : ''}${Math.round(dev * 100)}% vs usual ${perLabel(it.usual, it.unit)}`);
    chk.classList.add(Math.abs(dev) < 0.15 ? 'ok' : Math.abs(dev) > 0.6 ? 'bad' : 'warn');
  }
  function total() {
    const rows = [...body.querySelectorAll('tr')];
    const n = rows.filter(tr => itemOf(tr) && parseFloat(tr.querySelector('[data-l-amt]').value) >= 0).length;
    const t = rows.reduce((s, tr) => s + (parseFloat(tr.querySelector('[data-l-amt]').value) || 0), 0);
    $('[data-pu-total]').textContent = n ? `${n} line${n > 1 ? 's' : ''} · ${rs(t)}` : '';
  }

  body.addEventListener('input', e => { if (e.target.matches('[data-l-item]')) showSug(e.target); });
  body.addEventListener('focusin', e => { if (e.target.matches('[data-l-item]') && e.target.value.trim()) showSug(e.target); });
  body.addEventListener('focusout', e => {
    if (!e.target.matches('[data-l-item]')) return;
    // Leaving with a near-exact name: snap it to the item (e.g. "paneer " → "Paneer").
    const input = e.target, tr = input.closest('tr');
    if (input.value.trim() && !itemOf(tr)) { const r = rank(input.value); if (r.length === 1 || (r.length && norm(r[0].name) === norm(input.value))) { input.value = r[0].name; check(tr); total(); } }
    setTimeout(() => { if (sugFor === input) hideSug(); }, 0);
  });
  body.addEventListener('keydown', e => {
    if (!sugFor || e.target !== sugFor) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); moveSug(e.key === 'ArrowDown' ? 1 : -1); e.stopImmediatePropagation(); }
    else if (e.key === 'Enter' || (e.key === 'Tab' && !e.shiftKey)) { e.preventDefault(); e.stopImmediatePropagation(); pick(sugList[sugAt]); }
    else if (e.key === 'Escape') { hideSug(); e.stopImmediatePropagation(); }
  });
  body.addEventListener('input', e => { const tr = e.target.closest('tr'); if (tr) { check(tr); total(); } });
  body.addEventListener('change', e => { const tr = e.target.closest('tr'); if (tr) { check(tr); total(); } });
  body.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const tr = e.target.closest('tr');
    if (e.target.matches('[data-l-amt]')) { const nxt = tr.nextElementSibling || addLine(false); nxt.querySelector('[data-l-item]').focus(); }
    else { const f = [...tr.querySelectorAll('input')]; const i = f.indexOf(e.target); (f[i + 1] || f[0]).focus(); }
  });
  body.addEventListener('click', e => {
    if (!e.target.matches('[data-l-del]')) return;
    e.target.closest('tr').remove();
    if (!body.children.length) addLine(false);
    total();
  });
  for (let i = 0; i < 3; i++) addLine(false);
  if (D.sel && byId.get(D.sel)) {
    const tr = body.querySelector('tr'); tr.querySelector('[data-l-item]').value = byId.get(D.sel).name; check(tr);
    tr.querySelector('[data-l-qty]').focus();
  }
  $('[data-pu-focus]').addEventListener('click', () => setTimeout(() => body.querySelector('[data-l-item]').focus(), 50));
  $('[data-pu-add-item]').addEventListener('click', e => { e.preventDefault(); const f = $('[data-pu-quick]'); f.hidden = !f.hidden; if (!f.hidden) f.querySelector('input').focus(); });

  async function save(override) {
    const lines = [];
    for (const tr of body.querySelectorAll('tr')) {
      const name = tr.querySelector('[data-l-item]').value.trim();
      const q = tr.querySelector('[data-l-qty]').value.trim(), a = tr.querySelector('[data-l-amt]').value.trim();
      if (!name && !q && !a) continue;
      const it = itemOf(tr);
      if (!it) { alert(`"${name}" isn't in the item list. Pick it from the suggestions or add it.`); return; }
      if (!(parseFloat(q) > 0) || !(parseFloat(a) >= 0)) { alert(`${it.name}: type the quantity and the amount.`); return; }
      const qi = toItemUnit(parseFloat(q), tr.querySelector('[data-l-unit]').value, it);
      lines.push({ ingredient_id: it.id, qty: +qi.toFixed(4), unit: it.unit, amount: parseFloat(a) });
    }
    if (!lines.length) { alert('Add at least one line.'); return; }
    const off = [...body.querySelectorAll('.pu-chk.bad')].length;
    if (!override && off && !confirm(`${off} line${off > 1 ? 's are' : ' is'} far from the usual price. Save anyway?`)) return;
    const btn = $('[data-pu-save]'); btn.disabled = true;
    try {
      const res = await fetch('/purchases/bill', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: $('[data-pu-date]').value, vendor: $('[data-pu-vendor]').value, bill_ref: $('[data-pu-ref]').value,
          usage_type: $('[data-pu-usage]').value, override: !!override, lines }),
      });
      const j = await res.json().catch(() => ({}));
      if (res.status === 409 && j.duplicates) {
        if (confirm(`These look already logged:\n\n• ${j.duplicates.join('\n• ')}\n\nSave the bill anyway?`)) { btn.disabled = false; return save(true); }
        return;
      }
      if (!res.ok) { alert(j.error || 'Not saved'); return; }
      location.href = `/purchases?notice=${encodeURIComponent(`Bill saved: ${j.created} line${j.created > 1 ? 's' : ''}.`)}`;
    } finally { btn.disabled = false; }
  }
  $('[data-pu-save]').addEventListener('click', () => save(false));

  root.querySelectorAll('[data-pu-perpiece]').forEach(f => f.addEventListener('submit', async e => {
    e.preventDefault();
    const kg = parseFloat(f.querySelector('input').value);
    if (!(kg > 0)) return;
    if (!confirm(`Convert ${f.dataset.name}'s piece entries at ${kg} kg per piece?`)) return;
    const res = await fetch('/purchases/fix-per-piece', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ingredient_id: +f.dataset.puPerpiece, kg_per_piece: kg }) });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) { alert(j.error || 'Not saved'); return; }
    location.href = `/purchases?notice=${encodeURIComponent(`${j.fixed} ${f.dataset.name} line(s) converted.`)}#need-fixing`;
  }));

  const search = $('[data-pu-search]');
  if (search) search.addEventListener('input', () => {
    const q = search.value.trim().toLowerCase();
    let shown = 0;
    root.querySelectorAll('[data-pu-bill]').forEach(b => { const on = !q || b.dataset.text.includes(q); b.hidden = !on; if (on) shown++; });
    root.querySelectorAll('[data-pu-day]').forEach(d => {
      let n = d.nextElementSibling, any = false;
      while (n && n.matches('[data-pu-bill]')) { if (!n.hidden) any = true; n = n.nextElementSibling; }
      d.hidden = !any;
    });
    $('[data-pu-nomatch]').hidden = shown > 0;
  });
})();
