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

  async function readDay(day) {
    let form = null;
    for (let i = 0; i < 40 && !(form = document.getElementById('order_search')); i++) await sleep(500);
    if (!form) return { ok: false, error: 'The All Orders page did not load (signed in?)' };
    const params = new URLSearchParams(new FormData(form));
    params.set('data[Order][startdate]', petpoojaDate(day, 0));
    params.set('data[Order][enddate]', petpoojaDate(day, 1));
    const rows = [];
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
      }
      if (total !== null && rows.length >= total) break;
    }
    if (total !== null && rows.length !== total) return { ok: false, error: `Read ${rows.length} of ${total} orders` };
    if (!rows.length) return { ok: false, error: 'The All Orders list was empty' };
    return { ok: true, rows };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.cmd !== 'olread') return;
    readDay(msg.day).then(sendResponse, (e) => sendResponse({ ok: false, error: String(e) }));
    return true; // answer asynchronously
  });
})();
