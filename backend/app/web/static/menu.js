// Menu Analysis (templates/results.html): matrix chart, group filter, sortable
// dish table, and linking unmatched Petpooja names to menu items.
(function () {
  const D = JSON.parse(document.getElementById('ma-data').textContent);
  const ITEMS = D.items || [];
  if (!ITEMS.length) return;
  const G = { S: 'Stars', W: 'Workhorses', P: 'Puzzles', D: 'Dogs' };
  const C = { S: 'var(--ma-s)', W: 'var(--ma-w)', P: 'var(--ma-p)', D: 'var(--ma-d)' };
  const rs = n => '₹' + Math.round(n).toLocaleString('en-IN');
  const esc = t => String(t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fcc = f => (f > 45 ? 'hi' : f > 35 ? 'mid' : 'ok');
  let gsel = '';

  // ── Matrix: x = plates (log), y = contribution per plate
  const svg = document.querySelector('[data-ma-svg]'), tip = document.querySelector('[data-ma-tip]'), box = document.querySelector('[data-ma-mx]');
  const xmax = Math.max(10, ...ITEMS.map(i => i.q)) * 1.15;
  const ymax = Math.ceil(Math.max(...ITEMS.map(i => i.cpu)) / 50) * 50 || 50;
  const topTc = Math.max(...ITEMS.map(i => i.tc));
  function draw() {
    const W = svg.clientWidth, H = svg.clientHeight, pad = 8;
    const xs = q => pad + Math.log10(Math.max(q, 1)) / Math.log10(xmax) * (W - 2 * pad);
    const ys = v => H - pad - Math.max(0, Math.min(v, ymax)) / ymax * (H - 2 * pad);
    let h = '';
    for (let v = 0; v <= ymax; v += ymax > 200 ? 100 : 50) h += `<line x1="0" x2="${W}" y1="${ys(v)}" y2="${ys(v)}" stroke="#F0EEE6"/><text class="ma-ax" x="-8" y="${ys(v) + 3}" text-anchor="end">${v}</text>`;
    [1, 3, 10, 30, 100, 300, 1000].filter(q => q <= xmax).forEach(q => { h += `<text class="ma-ax" x="${xs(q)}" y="${H + 16}" text-anchor="middle">${q}</text>`; });
    h += `<line x1="${xs(D.median_q)}" x2="${xs(D.median_q)}" y1="0" y2="${H}" stroke="#C9C4B4" stroke-dasharray="4 4"/>`;
    h += `<line x1="0" x2="${W}" y1="${ys(D.median_cpu)}" y2="${ys(D.median_cpu)}" stroke="#C9C4B4" stroke-dasharray="4 4"/>`;
    h += `<text class="ma-ql" x="${W - 6}" y="14" text-anchor="end">Stars</text><text class="ma-ql" x="6" y="14">Puzzles</text>`;
    h += `<text class="ma-ql" x="${W - 6}" y="${H - 8}" text-anchor="end">Workhorses</text><text class="ma-ql" x="6" y="${H - 8}">Dogs</text>`;
    ITEMS.map((i, ix) => [i, ix]).sort((a, b) => b[0].tc - a[0].tc).forEach(([i, ix]) => {
      const r = 4 + Math.sqrt(i.tc / topTc) * 11;
      const dim = gsel && i.k !== gsel ? ' fill-opacity=".15"' : ' fill-opacity=".85"';
      h += `<circle class="ma-dot" data-i="${ix}" cx="${xs(i.q)}" cy="${ys(i.cpu)}" r="${r.toFixed(1)}" fill="${C[i.k]}"${dim}/>`;
    });
    ITEMS.slice(0, 4).forEach((i, n) => {
      h += `<text class="ma-lbl" x="${xs(i.q) - 12}" y="${ys(i.cpu) + (n === 1 ? 22 : -12)}" text-anchor="end">${esc(i.n.replace(/^Combo \d+\w? - /, ''))}</text>`;
    });
    svg.innerHTML = h;
  }
  draw();
  addEventListener('resize', draw);
  svg.addEventListener('mousemove', e => {
    const d = e.target.closest('.ma-dot');
    if (!d) { tip.hidden = true; return; }
    const i = ITEMS[+d.dataset.i], b = box.getBoundingClientRect();
    tip.innerHTML = `<b>${esc(i.n)}</b><br>${i.q} plates · ${rs(i.ap)} avg · food cost ${i.prov ? '~' : ''}${i.f}%<br>${rs(i.cpu)}/plate · ${rs(i.tc)} contribution · ${G[i.k]}`;
    tip.hidden = false;
    tip.style.left = Math.max(0, Math.min(e.clientX - b.left + 12, b.width - 270)) + 'px';
    tip.style.top = (e.clientY - b.top - 64) + 'px';
  });
  svg.addEventListener('mouseleave', () => { tip.hidden = true; });
  tip.hidden = true;

  // ── Group filter (cards + chips stay in step)
  function setGroup(k) {
    gsel = k;
    document.querySelectorAll('[data-ma-g]').forEach(b => b.classList.toggle('on', b.dataset.maG === k));
    document.querySelectorAll('[data-ma-chip]').forEach(b => b.classList.toggle('on', b.dataset.maChip === k));
    draw(); table();
  }
  document.querySelectorAll('[data-ma-g]').forEach(b => b.addEventListener('click', () => {
    setGroup(gsel === b.dataset.maG ? '' : b.dataset.maG);
    document.querySelector('[data-ma-tb]').closest('.sl-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }));
  document.querySelectorAll('[data-ma-chip]').forEach(b => b.addEventListener('click', () => setGroup(b.dataset.maChip)));

  // ── Dish table
  let sk = 'tc', sd = -1;
  const q = document.querySelector('[data-ma-q]');
  function table() {
    const t = q.value.trim().toLowerCase();
    const rows = ITEMS.filter(i => (!gsel || i.k === gsel) && (!t || (i.n + ' ' + i.c).toLowerCase().includes(t)))
      .sort((a, b) => (typeof a[sk] === 'string' ? a[sk].localeCompare(b[sk]) : a[sk] - b[sk]) * sd);
    document.querySelector('[data-ma-cnt]').textContent = rows.length === ITEMS.length ? rows.length : `${rows.length} of ${ITEMS.length}`;
    document.querySelector('[data-ma-tb]').innerHTML = rows.map(i => {
      const d = i.pq ? Math.round((i.q / i.pq - 1) * 100) : null;
      return `<tr><td><b>${esc(i.n)}</b><small class="ma-cat">${esc(i.c)}</small></td>
        <td class="sl-hide-sm"><span class="ma-pill"><i style="background:${C[i.k]}"></i>${G[i.k]}</span></td>
        <td class="r"><b>${i.q}</b>${d === null ? '<small class="ma-tr">new</small>' : `<small class="ma-tr ${d >= 0 ? 'up' : 'dn'}">${d >= 0 ? '▲' : '▼'} ${Math.abs(d)}%</small>`}</td>
        <td class="r sl-hide-sm">${rs(i.ap)}</td>
        <td class="r"><span class="ma-fc ${fcc(i.f)}" ${i.prov ? 'title="Provisional: recipe costing not weigh-in confirmed yet"' : ''}>${i.prov ? '~' : ''}${i.f}%</span></td>
        <td class="r sl-hide-sm">${rs(i.cpu)}</td>
        <td class="r"><span class="ma-bar"><i style="width:${Math.max(0, i.tc / topTc * 100)}%"></i></span><b>${rs(i.tc)}</b></td></tr>`;
    }).join('') || '<tr><td colspan="7" class="sl-muted">No dishes match.</td></tr>';
    document.querySelectorAll('[data-ma-sort]').forEach(th => th.classList.toggle('on', th.dataset.maSort === sk));
  }
  document.querySelectorAll('[data-ma-sort]').forEach(th => th.addEventListener('click', () => {
    if (sk === th.dataset.maSort) sd = -sd; else { sk = th.dataset.maSort; sd = sk === 'n' ? 1 : -1; }
    table();
  }));
  q.addEventListener('input', table);
  table();

  // ── Link unmatched Petpooja names
  const more = document.querySelector('[data-ma-showmore]');
  if (more) more.addEventListener('click', () => { document.querySelectorAll('[data-ma-more]').forEach(f => { f.hidden = false; }); more.remove(); });
  document.querySelectorAll('[data-ma-link]').forEach(f => f.addEventListener('submit', async e => {
    e.preventDefault();
    const sel = f.querySelector('select'), btn = f.querySelector('button');
    if (!sel.value) { sel.focus(); return; }
    btn.disabled = true;
    try {
      const res = await fetch('/results/link', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
        body: JSON.stringify({ pos_name: f.dataset.maLink, menu_item_id: +sel.value }),
      });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(j.error || 'Not linked');
      f.classList.add('done');
      f.innerHTML = `<span class="ma-nm">${esc(f.dataset.maLink)} → <b>${esc(sel.options[sel.selectedIndex].text)}</b></span><span class="ma-ok">Linked · reload to recount</span>`;
    } catch (err) { alert(err.message); btn.disabled = false; }
  }));
})();
