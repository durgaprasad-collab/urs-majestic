// Stock Log (templates/stock_log.html): tabs, filters, search, and one-step
// quantity saving. Every quantity box saves on Enter or when it loses focus;
// there is no separate Save button.
(function () {
  const data = JSON.parse(document.getElementById('sl-data').textContent);
  const tonight = new Set(data.tonight);
  const savedTonight = new Set();
  let done = data.done;

  // ── Tabs
  function setTab(name) {
    document.querySelectorAll('[data-sl-tab]').forEach((b) => b.classList.toggle('on', b.dataset.slTab === name));
    document.querySelectorAll('[data-sl-panel]').forEach((p) => { p.hidden = p.dataset.slPanel !== name; });
  }
  document.querySelectorAll('[data-sl-tab]').forEach((b) => b.addEventListener('click', () => setTab(b.dataset.slTab)));
  document.querySelectorAll('[data-sl-tab-go]').forEach((b) => b.addEventListener('click', () => setTab(b.dataset.slTabGo)));

  // ── Overview filters + search
  let filter = 'all';
  const q = document.querySelector('[data-sl-q]');
  function applyFilter() {
    const term = (q.value || '').trim().toLowerCase();
    let shown = 0;
    document.querySelectorAll('[data-sl-cat]').forEach((body) => {
      let n = 0;
      body.querySelectorAll('[data-sl-row]').forEach((row) => {
        const flags = row.dataset.flags.split(/\s+/);
        const ok = (filter === 'all' || flags.includes(filter)) && (!term || row.dataset.name.includes(term));
        row.hidden = !ok;
        n += ok;
      });
      body.hidden = n === 0;
      shown += n;
    });
    document.querySelector('[data-sl-empty]').hidden = shown !== 0;
  }
  document.querySelectorAll('[data-sl-filter]').forEach((b) => b.addEventListener('click', () => {
    filter = b.dataset.slFilter;
    document.querySelectorAll('.sl-chip').forEach((c) => c.classList.toggle('on', c.dataset.slFilter === filter));
    setTab('overview');
    applyFilter();
  }));
  q.addEventListener('input', applyFilter);

  // ── One-step save
  async function save(input) {
    const raw = input.value.trim();
    if (raw === '' || raw === (input.dataset.orig || '')) return;
    const qty = Number(raw);
    if (Number.isNaN(qty) || qty < 0) { input.classList.add('bad'); return; }
    input.classList.remove('bad');
    input.disabled = true;
    try {
      const res = await fetch(`/stock-log/item/${input.dataset.slQty}/count`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
        body: JSON.stringify({ qty }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || 'Could not save');
      const id = Number(input.dataset.slQty);
      // Reflect the save on every box for this item (Overview and Count tabs).
      document.querySelectorAll(`[data-sl-qty="${id}"]`).forEach((box) => {
        box.dataset.orig = String(qty);
        box.value = String(qty);
        box.classList.add('saved');
        const wrap = box.parentElement;
        const ok = wrap.querySelector('[data-sl-ok]');
        if (ok) ok.hidden = false;
        const src = wrap.querySelector('[data-sl-src]');
        if (src) { src.textContent = 'Counted'; src.className = 'sl-src counted'; }
        const row = box.closest('tr');
        const last = row && row.querySelector('[data-sl-last]');
        if (last) last.textContent = `${qty} ${body.unit} · today · ${body.by}`;
        const upd = row && row.querySelector('[data-sl-upd]');
        if (upd) upd.textContent = `Updated just now · ${body.by}`;
      });
      // Done in "count these first": fade it and move it to the bottom.
      const firstSec = document.querySelector('[data-sl-section="first"]');
      const firstRow = firstSec && firstSec.querySelector(`[data-sl-qty="${id}"]`)?.closest('.sl-crow');
      if (firstRow && !firstRow.classList.contains('sl-done')) {
        firstRow.classList.add('sl-done');
        firstSec.appendChild(firstRow);
        const n = document.querySelector('[data-sl-first-n]');
        if (n) n.textContent = firstSec.querySelectorAll('.sl-crow:not(.sl-done)').length;
      }
      if (tonight.has(id) && !savedTonight.has(id)) {
        savedTonight.add(id);
        done += 1;
        document.querySelector('[data-sl-done]').textContent = done;
        document.querySelector('[data-sl-bar]').style.width = `${Math.min(100, done / data.total * 100)}%`;
      }
    } catch (err) {
      input.classList.add('bad');
      input.title = err.message;
    } finally {
      input.disabled = false;
    }
  }
  const boxes = () => Array.from(document.querySelectorAll('[data-sl-panel]:not([hidden]) [data-sl-qty]')).filter((b) => b.offsetParent !== null);
  document.querySelectorAll('[data-sl-qty]').forEach((input) => {
    input.addEventListener('change', () => save(input));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { input.value = input.dataset.orig || ''; input.blur(); }
      if (e.key === 'Enter') {
        e.preventDefault();
        // Enter saves and moves to the next box, like a paper count sheet.
        const list = boxes();
        const next = list[list.indexOf(input) + 1];
        if (next) next.focus(); else input.blur();
      }
    });
  });

  // ── Stock kept at home: type a count, or move some of it to the restaurant.
  const fmt = (n) => (n === null || n === undefined ? '' : String(Math.round(n * 1000) / 1000));
  function showBalances(id, body) {
    document.querySelectorAll(`[data-sl-qty="${id}"]`).forEach((box) => {
      box.value = fmt(body.restaurant); box.dataset.orig = fmt(body.restaurant);
    });
    const home = document.querySelector(`[data-sl-home="${id}"]`);
    if (home) { home.value = fmt(body.home); home.dataset.orig = fmt(body.home); }
    const panel = document.querySelector(`[data-sl-xfer="${id}"]`);
    if (panel) panel.dispatchEvent(new Event('refresh'));
    const box = document.querySelector(`[data-sl-homebox="${id}"]`);
    const upd = box && box.querySelector('[data-sl-home-upd]');
    if (upd) upd.textContent = `Home updated just now · ${body.by}`;
  }
  async function postQty(url, payload) {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(payload) });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || 'Could not save');
    return body;
  }
  document.querySelectorAll('[data-sl-home]').forEach((input) => {
    input.addEventListener('change', async () => {
      const raw = input.value.trim();
      if (raw === '' || raw === (input.dataset.orig || '')) return;
      const qty = Number(raw);
      if (Number.isNaN(qty) || qty < 0) { input.classList.add('bad'); return; }
      input.classList.remove('bad'); input.disabled = true;
      try {
        const body = await postQty(`/stock-log/item/${input.dataset.slHome}/home-count`, { qty });
        showBalances(input.dataset.slHome, body);
        input.classList.add('saved');
        const ok = input.parentElement.querySelector('[data-sl-home-ok]'); if (ok) ok.hidden = false;
      } catch (err) { input.classList.add('bad'); input.title = err.message; }
      finally { input.disabled = false; }
    });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); input.blur(); } });
  });
  // "Move some": pick a direction and a quantity (any amount up to what is there).
  document.querySelectorAll('[data-sl-xfer]').forEach((panel) => {
    const id = panel.dataset.slXfer;
    const homeBox = document.querySelector(`[data-sl-homebox="${id}"]`);
    const home = homeBox.querySelector('[data-sl-home]');
    const rest = panel.closest('tr').querySelector('[data-sl-qty]');
    const qty = panel.querySelector('[data-sl-xqty]');
    const err = panel.querySelector('[data-sl-err]');
    const avail = panel.querySelector('[data-sl-avail]');
    const go = panel.querySelector('[data-sl-go]');
    let to = 'restaurant';
    const unit = homeBox.dataset.unit;
    const have = () => Number((to === 'restaurant' ? home : rest).value) || 0;
    function refresh() {
      panel.querySelectorAll('[data-sl-dir]').forEach((d) => d.classList.toggle('on', d.dataset.slDir === to));
      avail.textContent = `${to === 'restaurant' ? 'At home' : 'At restaurant'} now: ${fmt(have()) || 0} ${unit}`;
      err.hidden = true;
    }
    panel.addEventListener('refresh', refresh);
    panel.querySelectorAll('[data-sl-dir]').forEach((d) => d.addEventListener('click', () => { to = d.dataset.slDir; refresh(); }));
    panel.querySelector('[data-sl-all]').addEventListener('click', () => { qty.value = fmt(have()); });
    panel.querySelector('[data-sl-cancel]').addEventListener('click', () => { panel.hidden = true; });
    document.querySelector(`[data-sl-move="${id}"]`).addEventListener('click', () => {
      panel.hidden = !panel.hidden;
      if (!panel.hidden) { refresh(); qty.focus(); }
    });
    qty.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go.click(); } });
    go.addEventListener('click', async () => {
      const n = Number(qty.value);
      err.hidden = true;
      if (!(n > 0)) { err.textContent = 'Enter a quantity above zero.'; err.hidden = false; return; }
      if (n > have() + 0.0001) { err.textContent = `Only ${fmt(have()) || 0} ${unit} there.`; err.hidden = false; return; }
      go.disabled = true;
      try {
        const body = await postQty(`/stock-log/item/${id}/move`, { qty: n, to: to === 'home' ? 'home' : 'restaurant' });
        showBalances(id, body);
        const row = panel.closest('tr');
        const src = row.querySelector('[data-sl-src]');
        if (src) { src.textContent = to === 'home' ? 'Moved to home' : 'Moved from home'; src.className = 'sl-src transfer'; }
        const upd = row.querySelector('[data-sl-upd]');
        if (upd) upd.textContent = `Updated just now · ${body.by}`;
        qty.value = '';
        panel.hidden = true;
      } catch (e) { err.textContent = e.message; err.hidden = false; }
      finally { go.disabled = false; }
    });
  });

  // ── Count tab sections
  document.querySelectorAll('[data-sl-sec]').forEach((a) => a.addEventListener('click', () => {
    document.querySelectorAll('[data-sl-sec]').forEach((x) => x.classList.toggle('on', x === a));
    document.querySelectorAll('[data-sl-section]').forEach((sec) => { sec.hidden = sec.dataset.slSection !== a.dataset.slSec; });
  }));

  // ── Disable needs a confirmation
  document.querySelectorAll('[data-sl-disable]').forEach((form) => form.addEventListener('submit', (e) => {
    const ok = window.confirm(`Disable "${form.dataset.slDisable}"?\n\nIt disappears from the nightly count, order forecast, reorder lists and staff requests. History is kept and you can re-enable it from "Disabled items".`);
    if (!ok) e.preventDefault();
  }));
  // Close other open ⋯ menus when one opens.
  document.querySelectorAll('.sl-menu').forEach((d) => d.addEventListener('toggle', () => {
    if (d.open) document.querySelectorAll('.sl-menu[open]').forEach((o) => { if (o !== d) o.open = false; });
  }));
})();
