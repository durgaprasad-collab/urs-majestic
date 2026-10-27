// URS Live Stock Feed: every ~10 minutes (10:30 AM to midnight) refresh Petpooja's
// Item Report: Day Wise, read today's rows and send them to the admin's Live
// stock tab. Read only: it never clicks anything on Petpooja except reloading
// the report page, and it never writes to Petpooja.
const BASE = 'https://admin.ursmajestic.com';
const REPORT_URL = 'https://billing.petpooja.com/custom_reports/view_report/54';
const REPORT_PATTERN = 'https://billing.petpooja.com/custom_reports/view_report/*';
const OPEN_FROM = 10 * 60 + 30;  // 10:30 AM, local time
const OPEN_TO = 24 * 60 - 1;     // midnight: the report's date rolls over then, and the nightly upload covers the last hour

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const save = (o) => chrome.storage.local.set(o);
const load = (keys) => chrome.storage.local.get(keys);

function setBadge(ok) {
  chrome.action.setBadgeText({ text: ok ? '✓' : '!' });
  chrome.action.setBadgeBackgroundColor({ color: ok ? '#1E8A5A' : '#C2412D' });
}

async function fail(msg) {
  await save({ lastAt: Date.now(), lastOk: false, lastMsg: msg });
  setBadge(false);
  return { ok: false, error: msg };
}

function waitComplete(tabId, timeoutMs = 30000) {
  return new Promise((resolve) => {
    const done = (v) => { chrome.tabs.onUpdated.removeListener(on); clearTimeout(t); resolve(v); };
    const on = (id, info) => { if (id === tabId && info.status === 'complete') done(true); };
    const t = setTimeout(() => done(false), timeoutMs);
    chrome.tabs.onUpdated.addListener(on);
  });
}

async function reportTab() {
  const tabs = await chrome.tabs.query({ url: REPORT_PATTERN });
  if (tabs.length) {
    const tab = tabs[0];
    await chrome.tabs.reload(tab.id);          // a fresh load: today's date, newest rows
    await waitComplete(tab.id);
    return tab.id;
  }
  const tab = await chrome.tabs.create({ url: REPORT_URL, active: false, pinned: true });
  await waitComplete(tab.id);
  return tab.id;
}

async function readRows(tabId) {
  for (let i = 0; i < 4; i++) {            // the page script may need a moment to come up
    try {
      const res = await chrome.tabs.sendMessage(tabId, { cmd: 'read' });
      if (res) return res;
    } catch (_) { /* not ready yet */ }
    await sleep(3000);
  }
  return { ok: false, error: 'Could not read the report. Is Petpooja signed in?' };
}

async function run(force = false) {
  const { code } = await load(['code']);
  if (!code) return fail('No helper code saved yet');
  const d = new Date();
  const mins = d.getHours() * 60 + d.getMinutes();
  if (!force && (mins < OPEN_FROM || mins > OPEN_TO)) return { ok: true, skipped: 'outside opening hours' };

  const tabId = await reportTab();
  const res = await readRows(tabId);
  if (!res.ok) return fail(res.error || 'Read failed');
  if (!res.rows || !res.rows.length) {
    await save({ lastAt: Date.now(), lastOk: true, lastMsg: res.note || 'No sales in the report yet', lastDishes: 0 });
    setBadge(true);
    return { ok: true, empty: true };
  }
  let resp;
  try {
    resp = await fetch(`${BASE}/api/live-feed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${code}` },
      body: JSON.stringify({ rows: res.rows, read_at: new Date().toISOString() }),
    });
  } catch (e) {
    return fail('Could not reach admin.ursmajestic.com (offline?)');
  }
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) return fail(resp.status === 401 ? 'The helper code was not recognised. Make a new one on the Live stock page.' : (body.error || `Server said ${resp.status}`));
  await save({ lastAt: Date.now(), lastOk: true, lastMsg: `Sent ${body.rows} items, ${Math.round(body.dishes)} dishes`, lastDishes: body.dishes });
  setBadge(true);
  return { ok: true, ...body };
}

async function ping() {
  const { code } = await load(['code']);
  if (!code) return { ok: false, error: 'No helper code saved yet' };
  try {
    const r = await fetch(`${BASE}/api/live-feed/ping`, { headers: { Authorization: `Bearer ${code}` } });
    return r.ok ? { ok: true } : { ok: false, error: 'The helper code was not recognised' };
  } catch (_) {
    return { ok: false, error: 'Could not reach admin.ursmajestic.com' };
  }
}

function schedule() { chrome.alarms.create('urs-live', { delayInMinutes: 1, periodInMinutes: 10 }); }
chrome.runtime.onInstalled.addListener(schedule);
chrome.runtime.onStartup.addListener(schedule);
chrome.alarms.onAlarm.addListener((a) => { if (a.name === 'urs-live') run(false); });

chrome.runtime.onMessage.addListener((msg, _s, sendResponse) => {
  if (msg && msg.cmd === 'run-now') { run(true).then(sendResponse); return true; }
  if (msg && msg.cmd === 'test') { ping().then(sendResponse); return true; }
});
