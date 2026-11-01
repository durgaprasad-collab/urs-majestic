// Live band at the top of the Today page: today so far across Petpooja, Zomato and Swiggy.
// Reads the same /live-stock/data the Live stock tab uses, every minute, and only shows
// while there is a recent reading (restaurant hours). Read only.
(() => {
  const root = document.querySelector('[data-td-live]');
  if (!root) return;
  const be = Number(root.dataset.be) || 0;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const inr = (n) => '₹' + Math.round(n).toLocaleString('en-IN');
  const clock = (iso) => iso ? new Date(iso).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit', hour12: true }) : '';
  const ago = (m) => m < 1 ? 'just now' : m < 60 ? Math.round(m) + ' min' : Math.floor(m / 60) + ' h ' + Math.round(m % 60) + ' min';
  const CH = [['walkin', 'Walk-in', 'var(--td-walk)'], ['zomato', 'Zomato', 'var(--td-zom)'], ['swiggy', 'Swiggy', 'var(--td-swg)']];
  const SOURCE = { walkin: 'Petpooja', zomato: 'Zomato', swiggy: 'Swiggy' };

  function render(d) {
    const fresh = d && (d.status === 'live' || d.status === 'stale') && d.age_min < 150;
    root.hidden = !fresh;
    if (!fresh) return;
    const total = d.amount || 0;
    const chips = CH.map(([k]) => {
      const c = d[k];
      if (!c) return '<span class="tl-chip none"><i></i>' + SOURCE[k] + ' no read yet</span>';
      return '<span class="tl-chip ' + (c.status === 'live' ? 'ok' : 'old') + '"><i></i>' + SOURCE[k] + ' ' + ago(c.age_min) + '</span>';
    }).join('');
    const dishSplit = CH.filter(([k]) => d[k]).map(([k, , col]) => '<span><b style="background:' + col + '"></b>' + Math.round(d[k].dishes) + '</span>').join('');
    const mix = CH.filter(([k]) => d[k] && d[k].amount > 0).map(([k, , col]) => '<i style="flex:' + d[k].amount + ';background:' + col + '"></i>').join('');
    const mixTxt = CH.filter(([k]) => d[k]).map(([k, name]) => name + ' ' + inr(d[k].amount) + (d[k].orders ? ' (' + d[k].orders + ' orders)' : '')).join(' · ');
    const hours = d.hours || [];
    const peak = Math.max(1, ...hours.map((h) => h.n));
    const bars = hours.map((h) => '<i title="' + (h.h % 12 || 12) + (h.h % 24 >= 12 ? ' PM' : ' AM') + ': ' + Math.round(h.n) + ' dishes" style="height:' + Math.max(6, h.n / peak * 100) + '%"></i>').join('');
    const attn = [];
    CH.forEach(([k]) => { if (d[k] && d[k].status === 'stale') attn.push(SOURCE[k] + ' reading is ' + ago(d[k].age_min) + ' old'); });
    if ((d.unmapped || []).length) attn.push(d.unmapped.length + ' sold item' + (d.unmapped.length > 1 ? 's' : '') + ' not matched to the menu');
    if (d.status === 'live' && d.still_min >= 20) attn.push('No new sales since ' + clock(d.still_since));
    const pctBe = be ? Math.round(total / be * 100) : null;

    root.innerHTML =
      '<div class="tl-head"><h2>Live now <small>' + clock(d.now) + ' · today so far</small>'
      + '<a class="td-more" href="/live-stock">Open Live stock →</a></h2><div class="tl-chips">' + chips + '</div></div>'
      + '<div class="tl-tiles">'
      + '<div><div class="td-k">Sales so far</div><div class="td-vv">' + inr(total) + '</div><div class="td-vs">' + (pctBe !== null ? pctBe + '% of break-even ' + inr(be) : '') + '</div></div>'
      + '<div><div class="td-k">Dishes</div><div class="td-vv">' + Math.round(d.dishes) + '</div><div class="td-vs tl-split">' + dishSplit + '</div></div>'
      + '<div><div class="td-k">Stock out or tight</div><div class="td-vv ' + (d.out ? 'dn' : d.tight ? 'wn' : '') + '">' + (d.out + d.tight) + '</div><div class="td-vs">' + d.out + ' out · ' + d.tight + ' tight</div></div>'
      + '<div><div class="td-k">Last reading</div><div class="td-vv">' + clock(d.read_at) + '</div><div class="td-vs">' + ago(d.age_min) + ' ago</div></div>'
      + '</div>'
      + (mix ? '<div class="tl-mix" aria-hidden="true">' + mix + '</div><p class="tl-mixtxt">' + mixTxt + '</p>' : '')
      + '<div class="tl-lower">'
      + '<div><div class="td-k">Dishes by hour</div><div class="tl-bars">' + (bars || '<span class="td-muted">Not enough readings yet</span>') + '</div></div>'
      + '<div><div class="td-k">Needs attention</div>'
      + (attn.length ? attn.map((a) => '<p class="tl-attn">' + esc(a) + '</p>').join('') : '<p class="tl-attn ok">Nothing to fix right now</p>') + '</div>'
      + '</div>';
  }

  async function load() {
    try {
      const res = await fetch('/live-stock/data', { credentials: 'same-origin' });
      if (!res.ok) return;
      render(await res.json());
    } catch (_) { /* keep what is shown */ }
  }
  load();
  setInterval(() => { if (!document.hidden) load(); }, 60000);
})();
