// Runs on Petpooja's home page (dashboard). When background.js asks, it presses the
// dashboard's own Refresh button (the circular arrow beside the date), which pulls
// the latest orders into Petpooja's reports. That is the only thing it does here.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.cmd !== 'refresh') return;
  (async () => {
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    let btn = null;
    for (let i = 0; i < 30 && !btn; i++) {   // the dashboard loads its widgets after the page itself
      btn = document.querySelector('.dashboard-refresh-btn');
      if (!btn) await sleep(500);
    }
    if (!btn) return sendResponse({ ok: false, error: "Couldn't find Refresh on Petpooja's home page" });
    btn.click();
    await sleep(7000);                        // give Petpooja time to pull the latest orders in
    sendResponse({ ok: true });
  })();
  return true; // answer asynchronously
});
