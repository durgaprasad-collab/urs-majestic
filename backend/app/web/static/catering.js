// Catering: order lines (menu dishes + custom items), live money, save,
// stage changes and payments.
(function () {
  const root = document.querySelector('[data-ct]');
  if (!root) return;
  const D = JSON.parse(document.getElementById('ct-data').textContent);
  const start = JSON.parse(document.getElementById('ct-lines').textContent);
  const byName = new Map(D.menu.map(m => [m.name.toLowerCase(), m]));
  const byId = new Map(D.menu.map(m => [m.id, m]));
  const rs = n => '₹' + Math.round(n).toLocaleString('en-IN');
  const $ = s => root.querySelector(s);
  const body = $('[data-ct-lines]');
  document.getElementById('ct-menu').innerHTML = D.menu.map(m => `<option value="${m.name.replace(/"/g, '&quot;')}">`).join('');

  function addRow(line, custom) {
    const tr = document.createElement('tr');
    const isCustom = custom || (line && !line.menu_item_id);
    tr.dataset.custom = isCustom ? '1' : '';
    tr.innerHTML = `<td>${isCustom
        ? `<input data-c-name placeholder="Custom item, e.g. Curd Rice" value="${line ? line.item_name.replace(/"/g, '&quot;') : ''}"><span class="ct-tag">custom</span>`
        : `<input data-c-dish list="ct-menu" placeholder="Start typing a dish…" autocomplete="off" value="${line && byId.get(line.menu_item_id) ? byId.get(line.menu_item_id).name.replace(/"/g, '&quot;') : ''}">`}</td>
      <td class="r"><input data-c-qty inputmode="numeric" class="ct-num" value="${line ? line.quantity : ''}"></td>
      <td class="r sl-hide-sm sl-muted" data-c-menu></td>
      <td class="r"><input data-c-rate inputmode="decimal" class="ct-num" value="${line ? line.rate : ''}"></td>
      <td class="r"><b data-c-amt></b></td>
      <td class="r sl-hide-sm sl-muted" data-c-cost></td>
      <td><button type="button" class="ct-x" data-c-del aria-label="Remove">×</button></td>`;
    body.appendChild(tr);
    return tr;
  }
  const dishOf = tr => (tr.dataset.custom ? null : byName.get((tr.querySelector('[data-c-dish]').value || '').trim().toLowerCase()));

  function refresh() {
    let total = 0, atMenu = 0, cost = 0, uncosted = [];
    body.querySelectorAll('tr').forEach(tr => {
      const m = dishOf(tr), q = parseInt(tr.querySelector('[data-c-qty]').value) || 0;
      const rateIn = tr.querySelector('[data-c-rate]');
      if (m && rateIn.value === '' && !tr.dataset.priced) { rateIn.value = m.price; tr.dataset.priced = '1'; }
      const r = parseFloat(rateIn.value) || 0;
      tr.querySelector('[data-c-menu]').textContent = m ? rs(m.price) : '—';
      tr.querySelector('[data-c-amt]').textContent = q && r ? rs(q * r) : '';
      tr.querySelector('[data-c-cost]').textContent = m && m.cost != null && q ? rs(q * m.cost) : (q ? '—' : '');
      total += q * r;
      atMenu += q * (m ? m.price : r);
      if (m && m.cost != null) cost += q * m.cost;
      else if (q) uncosted.push(m ? m.name : (tr.querySelector('[data-c-name]') || {}).value || 'custom item');
    });
    const extra = Math.min(parseFloat($('[data-f="extra_discount"]').value) || 0, total);
    total -= extra;
    const plates = parseInt($('[data-f="plates"]').value) || 0;
    const margin = total - cost, pct = total ? margin / total * 100 : 0, disc = Math.max(atMenu - total, 0);
    $('[data-ct-money]').innerHTML = `
      <div><span>At menu prices</span><span>${rs(atMenu)}</span></div>
      <div><span>Catering discount</span><span>${disc ? '−' + rs(disc) + ` (${Math.round(disc / atMenu * 100)}%)` : '—'}</span></div>
      <div><span>Order total</span><b>${rs(total)}</b></div>
      ${plates ? `<div><span>Per plate (${plates})</span><span>${rs(total / plates)}</span></div>` : ''}
      <div><span>Food cost</span><span>${rs(cost)}</span></div>
      <div class="ct-bar"><i style="width:${Math.max(0, Math.min(100, pct))}%"></i></div>
      <div><span>Margin after food cost</span><span class="ct-g">${rs(margin)} · ${Math.round(pct)}%</span></div>
      ${uncosted.length ? `<p class="ct-note">No cost for: ${uncosted.join(', ')}. Margin leaves them out.</p>` : ''}`;
  }

  (start.length ? start : [null, null, null]).forEach(l => addRow(l, false));
  body.addEventListener('input', refresh);
  body.addEventListener('change', e => { if (e.target.matches('[data-c-dish]')) { const tr = e.target.closest('tr'); delete tr.dataset.priced; tr.querySelector('[data-c-rate]').value = ''; } refresh(); });
  body.addEventListener('click', e => { if (e.target.matches('[data-c-del]')) { e.target.closest('tr').remove(); refresh(); } });
  $('[data-f="plates"]').addEventListener('input', refresh);
  $('[data-f="extra_discount"]').addEventListener('input', refresh);
  $('[data-ct-add]').addEventListener('click', () => addRow(null, false).querySelector('input').focus());
  $('[data-ct-custom]').addEventListener('click', () => addRow(null, true).querySelector('input').focus());
  refresh();

  const post = async (url, body) => {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.error || 'Not saved');
    return j;
  };

  $('[data-ct-save]').addEventListener('click', async e => {
    const lines = [];
    for (const tr of body.querySelectorAll('tr')) {
      const q = parseInt(tr.querySelector('[data-c-qty]').value) || 0, r = tr.querySelector('[data-c-rate]').value;
      if (tr.dataset.custom) {
        const name = tr.querySelector('[data-c-name]').value.trim();
        if (!name && !q) continue;
        lines.push({ item_name: name, quantity: q, rate: r, unit: 'portions' });
      } else {
        const txt = tr.querySelector('[data-c-dish]').value.trim();
        if (!txt && !q) continue;
        const m = dishOf(tr);
        if (!m) { alert(`"${txt}" isn't on the menu. Pick it from the suggestions, or use + Custom item.`); return; }
        lines.push({ menu_item_id: m.id, quantity: q, rate: r });
      }
    }
    const f = {};
    root.querySelectorAll('[data-f]').forEach(i => (f[i.dataset.f] = i.value));
    e.target.disabled = true;
    try {
      const j = await post('/catering-orders/save', { id: D.id, ...f, lines });
      location.href = `/catering-orders?id=${j.id}&notice=${encodeURIComponent('Order saved.')}`;
    } catch (err) { alert(err.message); } finally { e.target.disabled = false; }
  });

  const go = async (body) => {
    try { await post(`/catering-orders/${D.id}/status`, body); location.href = `/catering-orders?id=${D.id}`; }
    catch (err) { alert(err.message); }
  };
  root.querySelectorAll('[data-ct-status]').forEach(b => b.addEventListener('click', () => {
    const s = b.dataset.ctStatus;
    if (s === 'cancelled' && !confirm('Cancel this order?')) return;
    go({ status: s });
  }));
  const confirmBtn = $('[data-ct-confirm]');
  if (confirmBtn) confirmBtn.addEventListener('click', () => {
    const a = prompt('Advance received (₹)? Leave 0 if none yet.', '0');
    if (a === null) return;
    go({ status: 'confirmed', advance: a || '0' });
  });
  const payBtn = $('[data-ct-pay]');
  if (payBtn) payBtn.addEventListener('click', async () => {
    const a = prompt('Amount received now (₹)?');
    if (!a) return;
    try { await post(`/catering-orders/${D.id}/payment`, { amount: a }); location.href = `/catering-orders?id=${D.id}`; }
    catch (err) { alert(err.message); }
  });
})();
