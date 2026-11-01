// Runs inside Swiggy's partner page itself (MAIN world), from the very start of the page.
// It notes the headers the page's own app uses for its rms.swiggy.com order requests (they carry
// the signed-in session's access token), and, when content script swiggy.js asks, uses them to
// fetch today's order history directly: the same "Past Orders" list the page shows.
// Read only: it only fetches the order history.
(() => {
  if (window.__ursS) return;
  const st = (window.__ursS = { headers: null, resId: null });

  const of = window.fetch;
  window.fetch = function (input, init) {
    try {
      const url = String((input && input.url) || input);
      if (/rms\.swiggy\.com\/orders\//.test(url)) {
        let h = (init && init.headers) || (input && input.headers) || {};
        if (h && typeof h.forEach === 'function' && !Array.isArray(h)) { const x = {}; h.forEach((v, k) => { x[k] = v; }); h = x; }
        if (Object.keys(h).some((k) => /accesstoken/i.test(k))) st.headers = Object.assign({}, h);
        const m = url.match(/[?&]restaurant_id=(\d+)/);
        if (m) st.resId = m[1];
      }
    } catch (_) { /* never get in the page's way */ }
    return of.apply(this, arguments);
  };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const pad = (n) => String(n).padStart(2, '0');
  const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

  // One history entry -> {id, status, placed, lines, total} for the live view, plus the tax, discount and
  // priced items the nightly upload needs (net sales = item GST / 5%, like Swiggy's own CSV).
  function fromOrder(o) {
    const t = o && o.status && o.status.ordered_time;      // 'YYYY-MM-DD HH:MM:SS', the restaurant's local time
    if (!t || !o.order_id) return null;
    const lines = [], items = [];
    for (const it of (o.cart && o.cart.items) || []) {
      const qty = Number(it.quantity) || 0;
      if (it.name && qty > 0) { lines.push({ name: String(it.name), qty }); items.push({ name: String(it.name), qty, total: Number(it.total) || 0 }); }
    }
    const g = o.GST_details || {};
    return { id: String(o.order_id), status: String(o.status.order_status || '').toUpperCase(),
             placed: String(t).replace(' ', 'T').slice(0, 19), lines, total: Number(o.bill) || 0,
             tax: [Number(g.item_SGST) || 0, Number(g.item_CGST) || 0, Number(g.item_IGST) || 0],
             trade: Number(o.restaurant_trade_discount) || 0, offers: Number(o.restaurant_offers_discount) || 0, items };
  }

  async function read(day, resIdHint, calendar) {
    // The app makes its order requests a few seconds in (slower in a background tab), so wait by the clock.
    const until = Date.now() + 30000;
    while (Date.now() < until && !st.headers) await sleep(500);
    if (!st.headers) return { ok: false, error: "the page didn't make its usual requests (signed in?)" };
    const resId = st.resId || resIdHint;
    if (!resId) return { ok: false, error: 'restaurant id not known yet' };
    // The list filters by calendar day. The live view's business day runs 1 AM to 1 AM, so it asks for both
    // days; the nightly upload wants the calendar day alone, as Swiggy's own CSV does.
    const next = new Date(day + 'T12:00:00'); next.setDate(next.getDate() + (calendar ? 0 : 1));
    const orders = {};
    for (let offset = 0, guard = 0; guard < 15; guard++) {
      const url = `https://rms.swiggy.com/orders/v1/history?limit=20&offset=${offset}&ordered_time__gte=${day}&ordered_time__lte=${ymd(next)}&restaurant_id=${resId}`;
      const r = await fetch(url, { method: 'GET', credentials: 'same-origin', headers: st.headers });
      if (!r.ok) return { ok: false, error: `Swiggy answered ${r.status}` };
      const j = await r.json();
      const page = ((j.data || [])[0] || {}).data || {};
      const list = page.objects || [];
      for (const o of list) { const x = fromOrder(o); if (x) orders[x.id] = x; }
      if (!list.length || !(page.meta && page.meta.next)) break;
      offset += list.length;
    }
    return { ok: true, resId, orders: Object.values(orders) };
  }

  window.addEventListener('message', async (ev) => {
    if (ev.source !== window || !ev.data || ev.data.type !== 'urs-s-read') return;
    let res;
    try { res = await read(ev.data.day, ev.data.resId, ev.data.calendar); } catch (e) { res = { ok: false, error: String(e).slice(0, 120) }; }
    window.postMessage({ type: 'urs-s-data', id: ev.data.id, ...res }, '*');
  });
})();
