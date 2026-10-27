// Isolated-world bridge: background.js asks for a read (with the business day's
// date range), page.js (inside the page) answers, and the rows go back to
// background.js, which sends them on.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.cmd !== 'read') return;
  const id = String(Date.now()) + Math.random();
  const timer = setTimeout(() => {
    window.removeEventListener('message', onMsg);
    sendResponse({ ok: false, error: 'The report did not answer in time' });
  }, 40000);
  function onMsg(ev) {
    if (ev.source !== window || !ev.data || ev.data.type !== 'urs-live-data' || ev.data.id !== id) return;
    clearTimeout(timer);
    window.removeEventListener('message', onMsg);
    sendResponse({ ok: ev.data.ok, rows: ev.data.rows, error: ev.data.error, note: ev.data.note });
  }
  window.addEventListener('message', onMsg);
  window.postMessage({ type: 'urs-live-read', id, range: msg.range }, '*');
  return true; // answer asynchronously
});
