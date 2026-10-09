// Buy inbox: tabs, selection + live totals, approve, share on WhatsApp.
(function () {
  const root = document.querySelector('[data-by]');
  if (!root) return;
  const $$ = (s, el = root) => Array.from(el.querySelectorAll(s));
  const rows = $$('[data-by-row]');
  const rs = n => '₹' + Math.round(n).toLocaleString('en-IN');
  const WHERE = { Vegetables: 'Market · vegetables', Dairy: 'Dairy', Frozen: 'Frozen', Utilities: 'Packaging', packaging: 'Packaging' };

  const qtyOf = tr => parseFloat(tr.querySelector('[data-by-qty]').value) || 0;
  const unitOf = tr => tr.dataset.unit || (tr.querySelector('[data-by-unit]') || {}).value || '';
  const costOf = tr => (parseFloat(tr.dataset.uc) || 0) * qtyOf(tr);
  const selected = () => rows.filter(tr => tr.querySelector('[data-by-cb]').checked);
  const show = (q, u) => u === 'g' && q >= 1000 ? +(q / 1000).toFixed(2) + ' kg'
    : u === 'ml' && q >= 1000 ? +(q / 1000).toFixed(2) + ' L' : +q.toFixed(2) + (u ? ' ' + u : '');

  function refresh() {
    const sel = selected();
    const total = sel.reduce((s, tr) => s + costOf(tr), 0);
    rows.forEach(tr => {
      tr.classList.toggle('by-off', !tr.querySelector('[data-by-cb]').checked);
      const c = costOf(tr);
      tr.querySelector('[data-by-cost]').textContent = c ? rs(c) : '—';
    });
    $$('[data-by-group]').forEach(tb => {
      const r = $$('[data-by-row]', tb), on = r.filter(tr => tr.querySelector('[data-by-cb]').checked);
      tb.querySelector('[data-by-gsum]').textContent = `${r.length} items · ${rs(on.reduce((s, tr) => s + costOf(tr), 0))}`;
    });
    const label = sel.length ? `${sel.length} · ${rs(total)}` : '';
    $$('[data-by-hbtn]').forEach(e => (e.textContent = label));
    $$('[data-by-total]').forEach(e => (e.textContent = rs(total)));
    $$('[data-by-count]').forEach(e => (e.textContent = `${sel.length} items selected`));
    $$('[data-by-bar-n]').forEach(e => (e.textContent = `${sel.length} items · ${rs(total)}`));
  }

  root.addEventListener('change', e => { if (e.target.matches('[data-by-cb], [data-by-unit]')) refresh(); });
  root.addEventListener('input', e => { if (e.target.matches('[data-by-qty]')) refresh(); });
  $$('[data-by-selall]').forEach(b => b.addEventListener('click', () => {
    const cbs = $$('[data-by-cb]', b.closest('tbody'));
    const all = cbs.every(c => c.checked);
    cbs.forEach(c => (c.checked = !all));
    refresh();
  }));

  function tab(t) {
    $$('[data-by-tab]').forEach(b => b.classList.toggle('on', b.dataset.byTab === t));
    $$('[data-by-panel]').forEach(p => (p.hidden = p.dataset.byPanel !== t));
    const bar = root.querySelector('[data-by-bar]');
    if (bar && rows.length) bar.hidden = t !== 'buy';
  }
  $$('[data-by-tab]').forEach(b => b.addEventListener('click', () => tab(b.dataset.byTab)));
  $$('[data-by-tab-go]').forEach(b => b.addEventListener('click', () => tab(b.dataset.byTabGo)));

  $$('[data-by-approve]').forEach(b => b.addEventListener('click', () => {
    const sel = selected();
    if (!sel.length) { alert('Tick at least one item to approve.'); return; }
    const missing = sel.filter(tr => !qtyOf(tr)).map(tr => tr.dataset.name);
    if (missing.length && !confirm(`No quantity for: ${missing.join(', ')}.\nApprove without a quantity?`)) return;
    root.querySelector('[data-by-items]').value = JSON.stringify(sel.map(tr => ({
      ingredient_id: tr.dataset.iid ? +tr.dataset.iid : null,
      requests: tr.dataset.req ? tr.dataset.req.split(',').map(Number) : [],
      qty: qtyOf(tr) || null,
      unit: unitOf(tr) || null,
    })));
    b.disabled = true;
    root.querySelector('[data-by-form]').submit();
  }));

  $$('[data-by-reject]').forEach(f => f.addEventListener('submit', e => {
    if (!confirm(`Reject the request for ${f.dataset.byReject}? The staff member is notified.`)) e.preventDefault();
  }));

  $$('[data-by-share]').forEach(b => b.addEventListener('click', () => {
    const groups = {};
    selected().forEach(tr => {
      const q = qtyOf(tr);
      (groups[tr.dataset.group] = groups[tr.dataset.group] || []).push(`• ${tr.dataset.name}${q ? ' – ' + show(q, unitOf(tr)) : ''}`);
    });
    $$('[data-by-wait]').forEach(tr => {
      const g = WHERE[tr.dataset.group] || 'Grocery & Hyperpure';
      (groups[g] = groups[g] || []).push(`• ${tr.dataset.name}${tr.dataset.qty ? ' – ' + tr.dataset.qty : ''}`);
    });
    const names = Object.keys(groups);
    if (!names.length) { alert('Nothing selected to share.'); return; }
    const day = new Date().toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' });
    let text = `*URS Majestic · to buy · ${day}*\n`;
    names.forEach(g => { text += `\n*${g}*\n${groups[g].join('\n')}\n`; });
    window.open('https://wa.me/?text=' + encodeURIComponent(text), '_blank', 'noopener');
  }));

  refresh();
})();
