// Runs on Petpooja's "All Orders" page. When background.js asks, it reads the finished business day's
// order list (the same list the manual "Order Listing" Excel export is made from), page by page, through
// the page's own search form, and hands the rows back. Read only: it only fetches the list.
(() => {
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const pad = (n) => String(n).padStart(2, '0');
  const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const petpoojaDate = (day, add) => {            // '2026-10-31' -> '31 Oct 2026 01:00:00' (the business day runs 1 AM to 1 AM)
    const d = new Date(day + 'T12:00:00'); d.setDate(d.getDate() + add);
    return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()} 01:00:00`;
  };
  const isoOf = (s) => {                          // '1 Nov 2026 00:50:05' -> '2026-11-01 00:50:05'
    const m = String(s).match(/(\d+) (\w{3}) (\d{4}) (\d\d:\d\d:\d\d)/);
    return m && MONTHS.includes(m[2]) ? `${m[3]}-${pad(MONTHS.indexOf(m[2]) + 1)}-${pad(m[1])} ${m[4]}` : null;
  };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const SOURCE_RE = /zomato|swiggy|ownly/i;
  const stored = (k) => new Promise((r) => chrome.storage.local.get(k, (v) => r(v || {})));

  // Platform orders: the list shows item names but not quantities, so each one's detail page is read (cached
  // per order, so a 10-minute read only fetches the new ones). The detail URL needs a second number that only
  // Petpooja's own page knows: learn it by opening one order's "view" panel once, and remember it.
  async function learnSegment() {
    const icon = document.querySelector('tbody .order-detail');   // any order on the page itself (the list rows read above are parsed copies)
    if (!icon) return null;
    const before = performance.getEntriesByType('resource').length;
    icon.click();
    for (let i = 0; i < 20; i++) {
      await sleep(500);
      const e = performance.getEntriesByType('resource').slice(before).find((x) => /\/orders\/order_detail\//.test(x.name));
      if (e) {
        const seg = new URL(e.name).pathname.split('/').pop();
        const close = document.querySelector('.modal.show .btn-close, .offcanvas.show .btn-close, [data-bs-dismiss]');
        if (close) close.click(); else document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        return seg;
      }
    }
    return null;
  }
  async function itemsOf(id, seg) {
    const r = await fetch(`/orders/order_detail/${id}/${seg}?fancybox=true&_=${Date.now()}`, { credentials: 'include', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
    if (!r.ok) return null;
    const d = new DOMParser().parseFromString(await r.text(), 'text/html');
    const tbl = [...d.querySelectorAll('table')].find((x) => /Item Name/i.test(x.textContent) && /Quantity/i.test(x.textContent));
    if (!tbl) return null;
    const items = [];
    for (const tr of tbl.querySelectorAll('tr')) {
      const c = [...tr.children].map((td) => td.textContent.trim().replace(/\s+/g, ' '));
      if (c.length >= 3 && c[0] && /^\d+(\.\d+)?$/.test(c[2])) items.push({ name: c[0], qty: Number(c[2]) });   // item rows only: tax / discount rows have no plain quantity
    }
    return items;
  }
  async function addItems(day, rows, trs) {
    const cache = (await stored('olItems')).olItems || {};
    let { olSeg } = await stored('olSeg');
    const todo = rows.map((r, i) => ({ r, tr: trs[i] })).filter(({ r }) => SOURCE_RE.test(r.type) && !/cancel|reject|fail|declin/i.test(r.status));
    for (const { r, tr } of todo) {
      const key = day + '|' + r.no;
      if (cache[key]) { r.items = cache[key]; continue; }
      const oc = tr.children[13] && tr.children[13].querySelector('.order-detail');
      const id = oc && (oc.getAttribute('onclick') || '').match(/\d{6,}/);
      if (!id) continue;
      if (!olSeg) { olSeg = await learnSegment(); if (olSeg) chrome.storage.local.set({ olSeg }); }
      if (!olSeg) break;
      let items = await itemsOf(id[0], olSeg);
      if (items === null) { olSeg = null; chrome.storage.local.remove('olSeg'); continue; }
      cache[key] = items;
      r.items = items;
    }
    const keep = Object.fromEntries(Object.entries(cache).filter(([k]) => k.startsWith(day)));   // only today's orders are worth remembering
    chrome.storage.local.set({ olItems: keep });
  }

  async function readDay(day, live) {
    let form = null;
    for (let i = 0; i < 40 && !(form = document.getElementById('order_search')); i++) await sleep(500);
    if (!form) return { ok: false, error: 'The All Orders page did not load (signed in?)' };
    const params = new URLSearchParams(new FormData(form));
    params.set('data[Order][startdate]', petpoojaDate(day, 0));
    params.set('data[Order][enddate]', petpoojaDate(day, 1));
    const rows = [], trsAll = [];
    let total = null;
    for (let p = 1; p <= 60; p++) {
      const r = await fetch(`/orders/ordlist/page:${p}`, {
        method: 'POST', credentials: 'include', body: params.toString(),
        headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest' },
      });
      if (!r.ok) return { ok: false, error: `Petpooja answered ${r.status}` };
      const html = await r.text();
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const trs = [...doc.querySelectorAll('tbody tr')].filter((tr) => tr.children.length >= 13);
      const m = html.match(/Showing\s+\d+\s+to\s+\d+\s+of\s+(\d+)\s+records/);
      if (m) total = Number(m[1]);
      if (!trs.length) break;
      for (const tr of trs) {
        const c = [...tr.children].map((td) => td.textContent.trim().replace(/\s+/g, ' '));
        const created = isoOf(c[12]);
        if (!created) return { ok: false, error: `Could not read the time of order ${c[1]}` };
        rows.push({ no: c[1], type: c[2], amount: c[6], discount: c[8], status: c[11], created });
        trsAll.push(tr);   // the parsed copy: only used for the order id, which the live fetch below re-reads from the page
      }
      if (total !== null && rows.length >= total) break;
    }
    if (total !== null && rows.length !== total) return { ok: false, error: `Read ${rows.length} of ${total} orders` };
    if (!rows.length) return { ok: false, error: 'The All Orders list was empty' };
    if (live) { try { await addItems(day, rows, trsAll); } catch (_) { /* items are a bonus: the split by source still goes */ } }
    return { ok: true, rows };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.cmd !== 'olread') return;
    readDay(msg.day, !!msg.live).then(sendResponse, (e) => sendResponse({ ok: false, error: String(e) }));
    return true; // answer asynchronously
  });
})();
