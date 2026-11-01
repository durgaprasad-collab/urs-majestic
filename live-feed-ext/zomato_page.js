// Runs inside Zomato's partner page itself (MAIN world), from the very start of the page.
// It notes the headers the page's own app uses for its Zomato requests (they carry the
// signed-in session's security tokens), and, when content script zomato.js asks, uses them
// to fetch today's order history directly: a few small requests instead of waiting for the
// list to draw and scrolling it. Read only: it only fetches the order history.
(() => {
  if (window.__ursZ) return;
  const st = (window.__ursZ = { headers: null, resId: null });

  const oo = XMLHttpRequest.prototype.open;
  const os = XMLHttpRequest.prototype.send;
  const sh = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function (m, u) { this.__ursU = String(u); this.__ursH = {}; return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) { if (this.__ursH) this.__ursH[k] = v; return sh.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (b) {
    try {
      if (/api\.zomato\.com\/merchant-gw\//.test(this.__ursU || '') && this.__ursH && Object.keys(this.__ursH).some((k) => /csrf/i.test(k))) {
        st.headers = Object.assign({}, this.__ursH);
      }
      const m = (typeof b === 'string' ? b : '').match(/"res_?[Ii]d"\s*:\s*"?(\d+)/) || (this.__ursU || '').match(/[?&]res_?[Ii]d=(\d+)/);
      if (m) st.resId = m[1];
    } catch (_) { /* never get in the page's way */ }
    return os.apply(this, arguments);
  };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const MONTHS = { january: 1, february: 2, march: 3, april: 4, may: 5, june: 6, july: 7, august: 8, september: 9, october: 10, november: 11, december: 12 };
  const pad = (n) => String(n).padStart(2, '0');
  // "<semibold-200|{white-500|DELIVERED}>" -> "DELIVERED"
  const plain = (t) => { const m = String(t || '').match(/\{[^{}|]*\|([^{}]*)\}/); return (m ? m[1] : String(t || '').replace(/<[^>]*>/g, '')).replace(/ /g, ' ').trim(); };

  function placedIso(hh, mm, ap, dd, monthName, refDay) {
    const month = MONTHS[String(monthName).toLowerCase()];
    if (!month) return null;
    const ref = new Date(refDay + 'T12:00:00');
    let best = null;
    for (const y of [ref.getFullYear() - 1, ref.getFullYear(), ref.getFullYear() + 1]) {
      const d = new Date(y, month - 1, Number(dd), 12, 0, 0);
      if (!best || Math.abs(d - ref) < Math.abs(best - ref)) best = d;
    }
    let h = Number(hh) % 12;
    if (ap === 'PM') h += 12;
    return `${best.getFullYear()}-${pad(best.getMonth() + 1)}-${pad(best.getDate())}T${pad(h)}:${mm}`;
  }

  // One history card -> {id, status, placed, items, total}
  function fromCard(s, refDay) {
    const t = plain(s.topRightText && s.topRightText.text).match(/(\d{1,2}):(\d{2}) ([AP]M) \| (\d{1,2}) ([A-Za-z]+)/);
    if (!t) return null;
    let items = '', total = 0;
    for (const row of s.infoList || []) {
      const left = plain(row.leftText && row.leftText.text);
      if (/^\d+\s*x\s/.test(left)) {
        items = left;
        total = parseFloat(plain(row.rightText && row.rightText.text).replace(/[^\d.]/g, '')) || 0;
      }
    }
    return { id: String(s.id), status: plain(s.primaryTag && s.primaryTag.label && s.primaryTag.label.text).toUpperCase(),
             placed: placedIso(t[1], t[2], t[3], t[4], t[5], refDay), items, total };
  }

  async function read(day, resIdHint) {
    // The app makes its first requests a few seconds in (slower in a background tab, where
    // Chrome slows timers), so wait by the clock. The restaurant id comes from the add-on's
    // memory (resIdHint) or from the page's own order-history request.
    const until = Date.now() + 30000;
    while (Date.now() < until && !(st.headers && (st.resId || resIdHint))) await sleep(500);
    if (!st.headers) return { ok: false, error: "the page didn't make its usual requests (signed in?)" };
    const resId = st.resId || resIdHint;
    if (!resId) return { ok: false, error: 'restaurant id not known yet' };
    const next = new Date(day + 'T12:00:00'); next.setDate(next.getDate() + 2);
    const from = `${day}T01:00`;
    const range = `${day},${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}`;
    const orders = {};
    let postback = '';
    for (let page = 0; page < 15; page++) {
      const r = await fetch('https://api.zomato.com/merchant-gw/web/order/history/get-all-v2', {
        method: 'POST', credentials: 'include', headers: st.headers,
        body: JSON.stringify({ res_Id: resId, limit: 10, order_type: '', created_at: range, postback_params: postback, state: '', rating: '', get_filters: false }),
      });
      if (!r.ok) return { ok: false, error: `Zomato answered ${r.status}` };
      const j = await r.json();
      let oldest = '9999';
      for (const s of j.snippets || []) {
        const o = fromCard(s, day);
        if (o && o.placed) { orders[o.id] = o; if (o.placed < oldest) oldest = o.placed; }
      }
      if (!j.hasMore || !j.postbackParams || oldest < from) break;   // newest first: anything older is yesterday
      postback = j.postbackParams;
    }
    return { ok: true, via: 'api', resId, orders: Object.values(orders) };
  }

  // The finished calendar day, with each order's full bill: Zomato's order-details data has what the
  // order-history CSV has (subtotal, the restaurant's discounts, the items). All or nothing: one order
  // that can't be read fails the whole day, so a partial day is never sent.
  async function readFinal(day, resIdHint) {
    const until = Date.now() + 30000;
    while (Date.now() < until && !(st.headers && (st.resId || resIdHint))) await sleep(500);
    if (!st.headers) return { ok: false, error: "the page didn't make its usual requests (signed in?)" };
    const resId = st.resId || resIdHint;
    if (!resId) return { ok: false, error: 'restaurant id not known yet' };
    const next = new Date(day + 'T12:00:00'); next.setDate(next.getDate() + 2);
    const range = `${day},${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}`;
    const ids = {};
    let postback = '';
    for (let page = 0; page < 25; page++) {
      const r = await fetch('https://api.zomato.com/merchant-gw/web/order/history/get-all-v2', {
        method: 'POST', credentials: 'include', headers: st.headers,
        body: JSON.stringify({ res_Id: resId, limit: 10, order_type: '', created_at: range, postback_params: postback, state: '', rating: '', get_filters: false }),
      });
      if (!r.ok) return { ok: false, error: `Zomato answered ${r.status}` };
      const j = await r.json();
      let oldest = '9999';
      for (const s of j.snippets || []) {
        const o = fromCard(s, day);
        if (o && o.placed) { if (o.placed.startsWith(day)) ids[o.id] = true; if (o.placed < oldest) oldest = o.placed; }
      }
      if (!j.hasMore || !j.postbackParams || oldest < `${day}T00:00`) break;
      postback = j.postbackParams;
    }
    const list = Object.keys(ids), orders = [];
    const one = async (id) => {
      const r = await fetch(`/merchant-api/orders/order-details?tab_id=${id}&view=order-history`, { credentials: 'include', headers: st.headers });
      if (!r.ok) throw new Error(`order ${id}: Zomato answered ${r.status}`);
      const j = await r.json();
      if (j.status !== 'success' || !j.order || !j.order.cartDetails) throw new Error(`order ${id}: no details`);
      const o = j.order, c = o.cartDetails;
      let pct = 0, abs = 0, salt = 0;
      for (const d of (c.discountApplied && c.discountApplied.discounts) || []) {
        for (const x of (d.discount && d.discount.offers) || []) { if (x.type === 'PERCENTAGE_DISCOUNT') pct += Number(x.offerAmount) || 0; else abs += Number(x.offerAmount) || 0; }
      }
      const dishes = (c.items && c.items.dishes) || [];
      for (const d of dishes) for (const x of d.calculations || []) if (x.entityType === 'DCT_SALT_DISCOUNT') salt += Number(x.amount) || 0;
      orders.push({ id: String(o.id), state: String(o.state || ''), created: o.createdAt, subtotal: Number(c.subtotal.amountDetails.totalCost) || 0,
                    salt, pct, abs, items: dishes.map((d) => ({ name: String(d.name), qty: Number(d.quantity) || 0 })) });
    };
    try {
      for (let i = 0; i < list.length; i += 4) await Promise.all(list.slice(i, i + 4).map(one));
    } catch (e) { return { ok: false, error: String((e && e.message) || e).slice(0, 120) }; }
    return { ok: true, resId, orders };
  }

  window.addEventListener('message', async (ev) => {
    if (ev.source !== window || !ev.data || ev.data.type !== 'urs-z-read') return;
    let res;
    try { res = ev.data.final ? await readFinal(ev.data.day, ev.data.resId) : await read(ev.data.day, ev.data.resId); } catch (e) { res = { ok: false, error: String(e).slice(0, 120) }; }
    window.postMessage({ type: 'urs-z-data', id: ev.data.id, ...res }, '*');
  });
})();
