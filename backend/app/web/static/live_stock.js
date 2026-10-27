(() => {
  const root = document.querySelector('[data-ls]');
  if (!root) return;
  const $ = (s) => root.querySelector(s);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const num = (v, u) => {
    if (v === null || v === undefined) return '—';
    if (u === 'pcs') return String(Math.round(v));
    const a = Math.abs(v);
    return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1).replace(/\.0$/, '') : a >= 0.1 ? v.toFixed(2) : v.toFixed(3);
  };
  const clock = (iso) => iso ? new Date(iso).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit', hour12: true }) : '—';
  const ago = (m) => m < 1 ? 'just now' : m < 60 ? Math.round(m) + ' min ago' : Math.floor(m / 60) + ' h ' + Math.round(m % 60) + ' min ago';

  function render(d) {
    const chip = $('[data-ls-chip]'), notice = $('[data-ls-notice]');
    const set = (cls, txt) => { chip.className = 'ls-chip ' + cls; chip.innerHTML = txt; };
    notice.hidden = true;
    if (d.status === 'none') {
      set('none', 'No read yet today');
      $('[data-ls-body]').innerHTML = '<tr><td colspan="7" class="sl-empty">' + (d.has_helper
        ? 'No reading has come in yet today. The helper reads every ~10 minutes while Chrome is open on the Petpooja item report.'
        : 'No helper is set up yet. Open "Chrome helper" below to set it up.') + '</td></tr>';
      ['k1', 'k2', 'k3', 'k4'].forEach((k) => ($('[data-ls-' + k + ']').textContent = '—'));
      return;
    }
    if (d.status === 'live') set('live', '<i></i> Live · read ' + ago(d.age_min));
    else if (d.status === 'stale') set('stale', '⚠ Stale · last read ' + ago(d.age_min));
    else set('none', 'Paused');
    if (d.notice) { notice.textContent = d.notice; notice.hidden = false; }
    if (d.status === 'stale') { notice.textContent = 'The last reading is ' + ago(d.age_min) + '. The numbers below are as of ' + clock(d.read_at) + ', not now. Is Chrome open on the Petpooja item report?'; notice.hidden = false; }
    if (d.status === 'live' && d.still_min >= 20) {
      notice.textContent = 'No new sales since ' + clock(d.still_since) + ' (at least ' + Math.round(d.still_min) + ' min). It may just be quiet, or Petpooja may be behind: its dashboard shows "Orders synced … ago". The numbers below are only as current as Petpooja\'s own sync.';
      notice.hidden = false;
    }
    $('[data-ls-lr]').textContent = clock(d.read_at);
    $('[data-ls-k1]').textContent = Math.round(d.dishes);
    $('[data-ls-k1s]').textContent = '₹' + Math.round(d.amount).toLocaleString('en-IN') + ' so far (walk-in)';
    $('[data-ls-k2]').textContent = clock(d.read_at);
    $('[data-ls-k2s]').textContent = ago(d.age_min) + ' · ' + d.reads_today + ' read' + (d.reads_today === 1 ? '' : 's') + ' today'
      + (d.still_min >= 20 ? ' · no new sales since ' + clock(d.still_since) : '');
    $('[data-ls-k3]').textContent = d.tight;
    $('[data-ls-k4]').textContent = d.out;
    const rows = d.rows || [];
    const body = rows.length ? rows.map((r) => {
      const pill = '<span class="ls-pill ' + r.status + '">' + esc(r.label) + '</span>';
      const since = r.since_count ? '<small>since your ' + esc(r.since_count) + ' count</small>' : '';
      const pct = r.opening > 0 ? Math.max(0, Math.min(100, r.now / r.opening * 100)) : 0;
      return '<tr class="ls-row"><td class="ls-item">' + esc(r.name) + '</td>'
        + '<td class="r sl-hide-sm">' + num(r.opening, r.unit) + ' ' + r.unit + '</td>'
        + '<td class="r ls-used">−' + num(r.used, r.unit) + since + '</td>'
        + '<td class="r"><b>' + num(Math.max(r.now, 0), r.unit) + '</b> ' + r.unit + '<div class="ls-bar"><i style="width:' + pct + '%"></i></div></td>'
        + '<td class="r sl-hide-sm">' + num(r.rest, r.unit) + '</td>'
        + '<td class="r sl-hide-sm">' + (r.close === null ? '—' : num(Math.max(r.close, 0), r.unit)) + '</td>'
        + '<td>' + pill + '</td></tr>';
    }).join('') : '<tr><td colspan="7" class="sl-empty">' + (d.status === 'ingested' ? 'Paused.' : 'Nothing to show yet.') + '</td></tr>';
    $('[data-ls-body]').innerHTML = body;
    if (d.day_share !== undefined && d.day_share < 0.25) $('[data-ls-note]').textContent = 'Early in the day, so "rest of today" and "at close" aren\'t shown yet.';

    const hrs = d.hours || [], mx = Math.max(1, ...hrs.map((h) => h.n));
    $('[data-ls-hrs]').innerHTML = hrs.map((h) => '<i style="height:' + Math.max(4, h.n / mx * 100) + '%" title="' + (h.h % 12 || 12) + (h.h % 24 >= 12 ? ' PM' : ' AM') + ': ' + h.n + ' dishes"></i>').join('');
    $('[data-ls-hl]').innerHTML = hrs.map((h) => '<span>' + (h.h % 12 || 12) + '</span>').join('');
    $('[data-ls-top]').innerHTML = (d.top || []).map((t) => '<div class="ls-dish"><span>' + esc(t.name) + '</span><b>' + Math.round(t.qty) + '</b></div>').join('');
    const unm = d.unmapped || [];
    $('[data-ls-unm-card]').hidden = !unm.length;
    $('[data-ls-unm]').innerHTML = unm.map((t) => '<div class="ls-dish"><span>' + esc(t.name) + '</span><b>' + Math.round(t.qty) + '</b></div>').join('');
  }

  let busy = false;
  async function load() {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const r = await fetch('/live-stock/data', { credentials: 'same-origin' });
      if (r.status === 401) { location.href = '/login?next=/live-stock'; return; }
      render(await r.json());
    } catch (e) {
      const chip = $('[data-ls-chip]');
      chip.className = 'ls-chip stale'; chip.textContent = '⚠ Can\'t reach the server';
    } finally { busy = false; }
  }
  load();
  setInterval(load, 30000);
  document.addEventListener('visibilitychange', load);

  const btn = $('[data-ls-code]');
  if (btn) btn.addEventListener('click', async () => {
    btn.disabled = true;
    try {
      const r = await fetch('/live-stock/helper-code', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ label: 'Chrome helper' }) });
      const j = await r.json();
      const box = $('[data-ls-codebox]');
      box.hidden = false;
      box.innerHTML = j.code ? '<code>' + esc(j.code) + '</code> <button type="button" class="sl-btn" data-ls-copy>Copy</button> <small>Shown once. Paste it into the add-on now.</small>' : esc(j.error || 'Not created');
      const cp = box.querySelector('[data-ls-copy]');
      if (cp) cp.addEventListener('click', () => { navigator.clipboard && navigator.clipboard.writeText(j.code); cp.textContent = 'Copied'; });
    } finally { btn.disabled = false; }
  });
})();
