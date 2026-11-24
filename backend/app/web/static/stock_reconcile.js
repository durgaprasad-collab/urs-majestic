// Filter chips and sortable columns for /stock-reconcile (rows are rendered by the server).
(() => {
  const tb = document.querySelector('[data-rc-table] tbody');
  if (!tb) return;
  const rows = [...tb.rows];
  document.querySelector('[data-rc-chips]').addEventListener('click', (e) => {
    const b = e.target.closest('.rc-chip');
    if (!b) return;
    document.querySelectorAll('.rc-chip').forEach((c) => c.setAttribute('aria-pressed', c === b ? 'true' : 'false'));
    rows.forEach((r) => { r.hidden = !!b.dataset.k && r.dataset.status !== b.dataset.k; });
  });
  let col = -1, asc = true;
  document.querySelectorAll('[data-rc-table] th').forEach((th, i) => th.addEventListener('click', () => {
    asc = col === i ? !asc : true; col = i;
    const num = th.dataset.t === 'n';
    const val = (r) => r.cells[i].dataset.v;
    rows.sort((a, b) => {
      const x = val(a), y = val(b);
      if (x === '' && y === '') return 0;
      if (x === '') return 1;
      if (y === '') return -1;
      return (num ? parseFloat(x) - parseFloat(y) : x.localeCompare(y)) * (asc ? 1 : -1);
    });
    rows.forEach((r) => tb.appendChild(r));
  }));
})();
