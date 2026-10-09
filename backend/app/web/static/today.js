// Today page charts: 21-day stacked channel bars with break-even / target
// step lines, and the month's cumulative sales against pace lines.
// Data is embedded by templates/today.html as JSON (#td-data).
(function () {
  const holder = document.getElementById('td-data');
  if (!holder) return;
  const { bars, month } = JSON.parse(holder.textContent);
  const NS = 'http://www.w3.org/2000/svg';
  const inr = (n) => '₹' + Math.round(n).toLocaleString('en-IN');
  const el = (t, a, p) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); if (p) p.appendChild(e); return e; };

  const tip = document.createElement('div');
  tip.className = 'td-tip';
  document.body.appendChild(tip);
  const show = (ev, html) => { tip.innerHTML = html; tip.style.opacity = 1; tip.style.left = Math.min(ev.clientX + 14, innerWidth - 230) + 'px'; tip.style.top = (ev.clientY + 14) + 'px'; };
  const hide = () => { tip.style.opacity = 0; };
  const niceMax = (v) => { const step = v > 30000 ? 10000 : 6000; return Math.ceil(v / step) * step; };

  // ── 21-day bars
  (function () {
    const svg = document.getElementById('td-bars');
    if (!svg) return;
    const W = 760, H = 270, L = 44, R = 74, T = 12, B = 34;
    const peak = Math.max(...bars.map((b) => Math.max(b.walk + b.zomato + b.swiggy, b.target)));
    const max = niceMax(peak * 1.05), step = max / 4;
    const pw = W - L - R, ph = H - T - B, slot = pw / bars.length, bw = Math.min(22, slot - 8);
    const y = (v) => T + ph - (v / max) * ph;
    for (let v = 0; v <= max; v += step) {
      el('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: v ? 'var(--td-grid)' : '#CFCBBE' }, svg);
      el('text', { x: L - 8, y: y(v) + 4, 'text-anchor': 'end' }, svg).textContent = v ? Math.round(v / 1000) + 'k' : '0';
    }
    bars.forEach((b, i) => {
      const cx = L + slot * i + slot / 2, x = cx - bw / 2;
      const parts = [[b.walk, 'var(--td-walk)'], [b.zomato, 'var(--td-zom)'], [b.swiggy, 'var(--td-swg)']].filter((p) => p[0] > 0);
      let acc = 0;
      parts.forEach(([v, c], k) => {
        const top = acc + v, h = y(acc) - y(top) - (acc ? 2 : 0);
        if (h > 0) el('rect', { x, y: y(top), width: bw, height: h, fill: c, rx: k === parts.length - 1 ? 4 : 0 }, svg);
        acc = top;
      });
      const weekend = b.dow === 'Sat' || b.dow === 'Sun';
      el('text', { x: cx, y: H - B + 15, 'text-anchor': 'middle', class: weekend ? 'lbl' : '' }, svg).textContent = b.dow[0];
      if (i % 3 === 0 || i === bars.length - 1) el('text', { x: cx, y: H - B + 28, 'text-anchor': 'middle' }, svg).textContent = b.label;
      const tot = b.walk + b.zomato + b.swiggy;
      const hit = el('rect', { x: cx - slot / 2, y: T, width: slot, height: ph, class: 'td-hit' }, svg);
      hit.addEventListener('mousemove', (ev) => show(ev,
        `<b>${b.dow} ${b.label} · ${inr(tot)}</b><i style="background:var(--td-walk)"></i>Walk-in ${inr(b.walk)}<br>` +
        `<i style="background:var(--td-zom)"></i>Zomato ${inr(b.zomato)}<br><i style="background:var(--td-swg)"></i>Swiggy ${inr(b.swiggy)}<br>` +
        `${tot >= b.be ? '✓ above' : '✗ below'} break-even (${inr(b.be)}) by ${inr(Math.abs(tot - b.be))}`));
      hit.addEventListener('mouseleave', hide);
    });
    // Step lines: break-even / target change when the month (days in month) changes.
    [['be', 'var(--td-ink)', 'Break-even'], ['target', 'var(--td-gold)', 'Target']].forEach(([key, color, name]) => {
      let d = '';
      bars.forEach((b, i) => {
        const x0 = L + slot * i, x1 = x0 + slot;
        d += (i === 0 ? `M${x0},${y(b[key])}` : ` V${y(b[key])}`) + ` H${x1}`;
      });
      el('path', { d, fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-dasharray': '6 4' }, svg);
      const last = bars[bars.length - 1][key];
      el('text', { x: W - R + 6, y: y(last) - 2, class: 'val' }, svg).textContent = (last / 1000).toFixed(1) + 'k';
      el('text', { x: W - R + 6, y: y(last) + 13 }, svg).textContent = name;
    });
    const tbl = document.getElementById('td-bars-table');
    tbl.innerHTML = '<table class="td-dt"><tr><th>Day</th><th>Walk-in</th><th>Zomato</th><th>Swiggy</th><th>Total</th><th>vs break-even</th></tr>' +
      bars.map((b) => { const t = b.walk + b.zomato + b.swiggy; return `<tr><td>${b.dow} ${b.label}</td><td>${inr(b.walk)}</td><td>${inr(b.zomato)}</td><td>${inr(b.swiggy)}</td><td><b>${inr(t)}</b></td><td class="${t >= b.be ? 'up' : 'dn'}">${t >= b.be ? '+' : '−'}${inr(Math.abs(t - b.be))}</td></tr>`; }).join('') + '</table>';
    const btn = document.querySelector('[data-td-table]');
    if (btn) btn.addEventListener('click', () => { tbl.hidden = !tbl.hidden; btn.textContent = tbl.hidden ? 'Show as table' : 'Hide table'; });
  })();

  // ── Month cumulative vs pace
  (function () {
    const svg = document.getElementById('td-pace');
    if (!svg || !month.cum.length) return;
    const W = 360, H = 230, L = 42, R = 12, T = 12, B = 26, days = month.days;
    const vmax = niceMax(Math.max(month.target * days, month.cum[month.cum.length - 1]) * 1.02);
    const x = (d) => L + (d / days) * (W - L - R), y = (v) => T + (H - T - B) - (v / vmax) * (H - T - B);
    for (let v = 0; v <= vmax; v += vmax / 3) {
      el('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: v ? 'var(--td-grid)' : '#CFCBBE' }, svg);
      el('text', { x: L - 6, y: y(v) + 4, 'text-anchor': 'end' }, svg).textContent = v ? (v / 100000).toFixed(1) + 'L' : '0';
    }
    [1, 8, 15, 22, days].forEach((d) => { el('text', { x: x(d), y: H - 8, 'text-anchor': 'middle' }, svg).textContent = d; });
    el('line', { x1: x(0), y1: y(0), x2: x(days), y2: y(month.be * days), stroke: 'var(--td-ink)', 'stroke-width': 2, 'stroke-dasharray': '6 4' }, svg);
    el('line', { x1: x(0), y1: y(0), x2: x(days), y2: y(month.target * days), stroke: 'var(--td-gold)', 'stroke-width': 2, 'stroke-dasharray': '6 4' }, svg);
    el('path', { d: `M${x(0)},${y(0)} ` + month.cum.map((v, i) => `L${x(i + 1)},${y(v)}`).join(' '), fill: 'none', stroke: '#0B3A2E', 'stroke-width': 2.5, 'stroke-linejoin': 'round' }, svg);
    const n = month.cum.length, last = month.cum[n - 1];
    el('circle', { cx: x(n), cy: y(last), r: 4.5, fill: '#0B3A2E', stroke: '#fff', 'stroke-width': 2 }, svg);
    el('text', { x: x(n) + 8, y: y(last) - 6, class: 'val' }, svg).textContent = (last / 100000).toFixed(2) + 'L';
    el('text', { x: x(days) - 2, y: y(month.be * days) + 14, 'text-anchor': 'end' }, svg).textContent = 'BE ' + (month.be * days / 100000).toFixed(2) + 'L';
    el('text', { x: x(days) - 2, y: y(month.target * days) - 6, 'text-anchor': 'end' }, svg).textContent = 'Target ' + (month.target * days / 100000).toFixed(2) + 'L';
    month.cum.forEach((v, i) => {
      const h = el('circle', { cx: x(i + 1), cy: y(v), r: 10, class: 'td-hit' }, svg);
      h.addEventListener('mousemove', (ev) => show(ev, `<b>Day ${i + 1} · ${inr(v)} so far</b>Break-even pace ${inr(month.be * (i + 1))}<br>Target pace ${inr(month.target * (i + 1))}`));
      h.addEventListener('mouseleave', hide);
    });
  })();
})();
