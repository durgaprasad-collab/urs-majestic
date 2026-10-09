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
      });
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
