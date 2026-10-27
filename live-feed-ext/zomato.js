// Runs on Zomato's partner "Order history" page. When background.js asks, it scrolls
// the order list from the top, collects every order (the list only keeps the orders on
// screen, so they are gathered by order ID as it scrolls) and returns the ones placed on
// the business day (1 AM to 1 AM). Read only: it only scrolls the list and reads text.
(() => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const MONTHS = { january: 1, february: 2, march: 3, april: 4, may: 5, june: 6, july: 7, august: 8, september: 9, october: 10, november: 11, december: 12 };
  // "DELIVERED \n 9:36 PM | 27 October \n ID: 877… \n By Kumar \n 1 x Chilli Parotta, 2 x Naan \n ₹171.00"
  const ORDER_RE = /([A-Z][A-Z ]{3,})\s*\n(?:\s*\d+\s*\n)?\s*(\d{1,2}):(\d{2}) ([AP]M) \| (\d{1,2}) ([A-Za-z]+)\s*\nID:\s*(\d+)\s*\nBy ([^\n]*)\n([\s\S]*?)₹\s*([\d,]+(?:\.\d+)?)/g;

  // 'YYYY-MM-DDTHH:MM' (restaurant local time) from the card's "9:36 PM" and "27 October".
  // The year isn't shown, so take the one that puts the date within a few days of the business day.
  function placedIso(time, hh, mm, ap, dd, monthName, refDay) {
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
    const p = (n) => String(n).padStart(2, '0');
    return `${best.getFullYear()}-${p(best.getMonth() + 1)}-${p(best.getDate())}T${p(h)}:${mm}`;
  }

  function parseOrders(text, refDay, into) {
    const clean = text.replace(/ /g, ' ');   // the page uses non-breaking spaces
    for (const m of clean.matchAll(ORDER_RE)) {
      const id = m[7];
      if (into[id]) continue;
      const placed = placedIso(null, m[2], m[3], m[4], m[5], m[6], refDay);
      if (!placed) continue;
      into[id] = { id, status: m[1].trim(), placed, items: m[9].replace(/\s+/g, ' ').trim(), total: parseFloat(m[10].replace(/,/g, '')) };
    }
    return into;
  }

  function findScroller() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) { if (/^ID:\s*\d+/.test(node.textContent.replace(/ /g, ' ').trim())) break; }
    let el = node && node.parentElement;
    while (el && el !== document.body) {
      const cs = getComputedStyle(el);
      if (/(auto|scroll)/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 5) return el;
      el = el.parentElement;
    }
    return null;
  }

  async function readOrders(refDay) {
    const next = new Date(refDay + 'T12:00:00'); next.setDate(next.getDate() + 1);
    const p = (n) => String(n).padStart(2, '0');
    const from = `${refDay}T01:00`;
    const to = `${next.getFullYear()}-${p(next.getMonth() + 1)}-${p(next.getDate())}T01:00`;
    // The list loads a few seconds after the page does.
    let sc = null;
    for (let i = 0; i < 50 && !sc; i++) { sc = findScroller(); if (!sc) await sleep(500); }
    if (!sc) {
      if (/No orders|no orders/.test(document.body.innerText)) return { ok: true, orders: [] };
      return { ok: false, error: "Couldn't find Zomato's order list. Is Zomato signed in in this Chrome?" };
    }
    const found = {};
    for (let i = 0; i < 5 && sc.scrollTop > 0; i++) { sc.scrollTop = 0; await sleep(700); sc = findScroller() || sc; }  // start from the newest order
    await sleep(800);
    let same = 0;
    // The date filter is only a day or two, so scroll the whole list; the day's own orders are picked out below.
    for (let step = 0; step < 80; step++) {
      sc = findScroller() || sc;                       // the list can be re-created as it re-renders
      const before = Object.keys(found).length;
      parseOrders(document.body.innerText, refDay, found);
      const atEnd = sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 5;
      if (atEnd) {                                     // nothing more to load once two looks at the end add nothing
        same = Object.keys(found).length === before ? same + 1 : 0;
        if (same >= 2) break;
        await sleep(900);
        continue;
      }
      sc.scrollTop = sc.scrollTop + Math.max(200, sc.clientHeight - 150);
      await sleep(1000);
    }
    parseOrders(document.body.innerText, refDay, found);
    const orders = Object.values(found).filter((o) => o.placed >= from && o.placed < to);
    return { ok: true, orders };
  }

  globalThis.__ursZomato = { parseOrders, placedIso, readOrders };   // for tests

  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
      if (!msg || msg.cmd !== 'zread') return;
      readOrders(msg.day).then(sendResponse, (e) => sendResponse({ ok: false, error: String(e) }));
      return true; // answer asynchronously
    });
  }
})();
