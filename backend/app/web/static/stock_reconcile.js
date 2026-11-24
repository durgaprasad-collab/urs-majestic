// Filter chips and sortable columns for /stock-reconcile (rows are rendered by the server).
(() => {
  const tb = document.querySelector('[data-sr-table] tbody');
  if (!tb) return;
  const rows = [...tb.rows];
  let status = '', term = '';
  const apply = () => rows.forEach((r) => {
    const name = r.cells[0].dataset.v.toLowerCase();
    r.hidden = (!!status && r.dataset.status !== status) || (!!term && !name.includes(term));
  });
  document.querySelector('[data-sr-chips]').addEventListener('click', (e) => {
    const b = e.target.closest('.sr-chip');
    if (!b) return;
    document.querySelectorAll('.sr-chip').forEach((c) => c.setAttribute('aria-pressed', c === b ? 'true' : 'false'));
    status = b.dataset.k || '';
    apply();
  });
  document.querySelector('[data-sr-search]').addEventListener('input', (e) => { term = e.target.value.trim().toLowerCase(); apply(); });
  let col = -1, asc = true;
  document.querySelectorAll('[data-sr-table] th').forEach((th, i) => th.addEventListener('click', () => {
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
