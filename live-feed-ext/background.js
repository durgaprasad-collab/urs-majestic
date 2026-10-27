// URS Live Stock Feed: every ~10 minutes, during the restaurant's hours only, read
// Petpooja's Item Report (Day Wise With Time) for today's business day (1 AM to
// 1 AM) and send the rows to the admin's Live stock tab. Read only: it reloads the
// report page and presses its Search button, and never writes to Petpooja.
const BASE = 'https://admin.ursmajestic.com';
const REPORT_URL = 'https://billing.petpooja.com/custom_reports/view_report/55';
const REPORT_PATTERN = 'https://billing.petpooja.com/custom_reports/view_report/*';
const DEFAULT_OPEN = '12:00';   // restaurant hours, PC's local time (orders run ~12:30 PM to ~12:30 AM)
const DEFAULT_CLOSE = '01:00';  // Petpooja's business day also ends at 1 AM

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const toMins = (hhmm) => { const [h, m] = String(hhmm).split(':').map(Number); return h * 60 + (m || 0); };
const pad = (n) => String(n).padStart(2, '0');
const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

// Inside opening hours? Handles hours that run past midnight (12:00 to 01:00).
function inHours(now, open, close) {
  const m = now.getHours() * 60 + now.getMinutes(), o = toMins(open), c = toMins(close);
  return o <= c ? (m >= o && m < c) : (m >= o || m < c);
}

// Petpooja's business day runs 1 AM to 1 AM, so 12:20 AM still belongs to yesterday.
function businessRange(now) {
  const start = new Date(now);
  if (now.getHours() < 1) start.setDate(start.getDate() - 1);
  const end = new Date(start);
  end.setDate(end.getDate() + 1);
  return { day: ymd(start), from: `${ymd(start)} 01:00:00`, to: `${ymd(end)} 01:00:00` };
}
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

async function readRows(tabId, range) {
  for (let i = 0; i < 4; i++) {            // the page script may need a moment to come up
    try {
      const res = await chrome.tabs.sendMessage(tabId, { cmd: 'read', range });
      if (res) return res;
    } catch (_) { /* not ready yet */ }
    await sleep(3000);
  }
  return { ok: false, error: 'Could not read the report. Is Petpooja signed in?' };
}

async function run(force = false) {
  const { code, open, close } = await load(['code', 'open', 'close']);
  if (!code) return fail('No helper code saved yet');
  const now = new Date();
  if (!force && !inHours(now, open || DEFAULT_OPEN, close || DEFAULT_CLOSE)) return { ok: true, skipped: 'outside restaurant hours' };

  const tabId = await reportTab();
  const res = await readRows(tabId, businessRange(now));
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
  if (msg && msg.cmd === 'hours') { load(['open', 'close']).then((s) => sendResponse({ open: s.open || DEFAULT_OPEN, close: s.close || DEFAULT_CLOSE })); return true; }
});
