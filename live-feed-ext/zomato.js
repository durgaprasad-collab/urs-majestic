// Runs on Zomato's partner "Order history" page. When background.js asks, it passes the
// request to zomato_page.js (which runs inside the page and fetches today's orders from
// Zomato's own order-history data in a few seconds) and hands back the answer.
// Read only: nothing on Zomato is changed.
(() => {
  const ask = (day, resId, timeoutMs = 50000) => new Promise((resolve) => {
    const id = Math.random().toString(36).slice(2);
    const on = (ev) => {
      if (ev.source !== window || !ev.data || ev.data.type !== 'urs-z-data' || ev.data.id !== id) return;
      window.removeEventListener('message', on); clearTimeout(t);
      const { type, id: _id, ...res } = ev.data;
      resolve(res);
    };
    const t = setTimeout(() => { window.removeEventListener('message', on); resolve({ ok: false, error: 'the page took too long to answer' }); }, timeoutMs);
    window.addEventListener('message', on);
    window.postMessage({ type: 'urs-z-read', id, day, resId }, '*');
  });

  async function readOrders(day) {
    const { zomatoResId } = await chrome.storage.local.get(['zomatoResId']);   // remembered from an earlier read
    const res = await ask(day, zomatoResId);
    if (res.ok && res.resId && res.resId !== zomatoResId) await chrome.storage.local.set({ zomatoResId: res.resId });
    if (!res.ok) {
      const t = document.body ? document.body.innerText.slice(0, 600) : '';
      const hint = /log ?in|sign ?in|password|otp/i.test(t) ? ' It looks like a sign-in page.' : '';
      return { ok: false, error: `${res.error}${hint}` };
    }
    const next = new Date(day + 'T12:00:00'); next.setDate(next.getDate() + 1);
    const p = (n) => String(n).padStart(2, '0');
    const from = `${day}T01:00`;
    const to = `${next.getFullYear()}-${p(next.getMonth() + 1)}-${p(next.getDate())}T01:00`;
    return { ok: true, orders: (res.orders || []).filter((o) => o.placed >= from && o.placed < to) };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.cmd !== 'zread') return;
    readOrders(msg.day).then(sendResponse, (e) => sendResponse({ ok: false, error: String(e) }));
    return true; // answer asynchronously
  });
})();
