// URS Live Stock Feed: every ~10 minutes, during the restaurant's hours only:
//   1. press Refresh on Petpooja's home page (so its reports aren't behind),
//   2. open Petpooja's Item Report (Day Wise With Time) for today's business day (1 AM to
//      1 AM), read its rows and send them to the admin's Live stock tab,
//   3. open Zomato's Order history, read today's orders and send them too.
// Read only: it presses Petpooja's Refresh and Search buttons, scrolls Zomato's order list,
// and never writes anything on either site.
const BASE = 'https://admin.ursmajestic.com';
const REPORT_URL = 'https://billing.petpooja.com/custom_reports/view_report/55';
const REPORT_PATTERN = 'https://billing.petpooja.com/custom_reports/view_report/*';
const DASHBOARD_URL = 'https://billing.petpooja.com/users/dashboard';
const DASHBOARD_PATTERN = 'https://billing.petpooja.com/users/dashboard*';
const ZOMATO_URL = 'https://www.zomato.com/partners/onlineordering/orderHistory/';
const ZOMATO_PATTERN = 'https://www.zomato.com/partners/onlineordering/*';
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
  return { ok: false, error: msg, msg };
}

function waitComplete(tabId, timeoutMs = 30000) {
  return new Promise((resolve) => {
    const done = (v) => { chrome.tabs.onUpdated.removeListener(on); clearTimeout(t); resolve(v); };
    const on = (id, info) => { if (id === tabId && info.status === 'complete') done(true); };
    const t = setTimeout(() => done(false), timeoutMs);
    chrome.tabs.onUpdated.addListener(on);
  });
}

// Take the helper's one tab to a page and wait for it to finish loading.
async function goto(tabId, url) {
  const loaded = waitComplete(tabId);
  await chrome.tabs.update(tabId, { url });   // same URL = a fresh load
  await loaded;
}

async function helperTab() {
  const tabs = await chrome.tabs.query({ url: [REPORT_PATTERN, DASHBOARD_PATTERN, ZOMATO_PATTERN] });
  if (tabs.length) return tabs[0].id;
  const tab = await chrome.tabs.create({ url: DASHBOARD_URL, active: false, pinned: true });
  await waitComplete(tab.id);
  return tab.id;
}

// Ask the page's script for something; it may need a moment to come up after a load.
async function ask(tabId, message, tries = 4) {
  for (let i = 0; i < tries; i++) {
    try {
      const res = await chrome.tabs.sendMessage(tabId, message);
      if (res) return res;
    } catch (_) { /* not ready yet */ }
    await sleep(3000);
  }
  return null;
}

async function post(code, body) {
  let resp;
  try {
    resp = await fetch(`${BASE}/api/live-feed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${code}` },
      body: JSON.stringify({ ...body, read_at: new Date().toISOString() }),
    });
  } catch (_) {
    return { ok: false, error: 'Could not reach admin.ursmajestic.com (offline?)' };
  }
  const json = await resp.json().catch(() => ({}));
  if (!resp.ok) return { ok: false, error: resp.status === 401 ? 'The helper code was not recognised. Make a new one on the Live stock page.' : (json.error || `Server said ${resp.status}`) };
  return { ok: true, ...json };
}

// Petpooja walk-in sales: Refresh on the home page, then the item report.
async function petpoojaPart(tabId, code, range) {
  await goto(tabId, DASHBOARD_URL);
  const refreshed = (await ask(tabId, { cmd: 'refresh' }, 3)) || { ok: false, error: "Couldn't press Refresh on Petpooja's home page" };
  await goto(tabId, REPORT_URL);
  const res = (await ask(tabId, { cmd: 'read', range })) || { ok: false, error: 'Could not read the report. Is Petpooja signed in?' };
  if (!res.ok) return { ok: false, msg: `Petpooja: ${res.error || 'read failed'}` };
  if (!res.rows || !res.rows.length) return { ok: true, empty: true, msg: 'Petpooja: no sales yet' };
  const sent = await post(code, { channel: 'petpooja', rows: res.rows });
  if (!sent.ok) return { ok: false, msg: `Petpooja: ${sent.error}` };
  return { ok: true, rows: sent.rows, dishes: sent.dishes, msg: `Petpooja: ${sent.rows} items, ${Math.round(sent.dishes)} dishes${refreshed.ok ? '' : ` (${refreshed.error})`}` };
}

// Zomato orders: the order history list, scrolled and read.
async function zomatoPart(tabId, code, range) {
  await goto(tabId, ZOMATO_URL);
  const res = (await ask(tabId, { cmd: 'zread', day: range.day }, 3)) || { ok: false, error: "Couldn't read Zomato. Is it signed in in this Chrome?" };
  if (!res.ok) return { ok: false, msg: `Zomato: ${res.error || 'read failed'}` };
  const sent = await post(code, { channel: 'zomato', orders: res.orders || [] });
  if (!sent.ok) return { ok: false, msg: `Zomato: ${sent.error}` };
  return { ok: true, orders: sent.orders, dishes: sent.dishes, msg: `Zomato: ${sent.orders} orders, ${Math.round(sent.dishes)} dishes` };
}

async function run(force = false) {
  const { code, open, close, zomato } = await load(['code', 'open', 'close', 'zomato']);
  if (!code) return fail('No helper code saved yet');
  const now = new Date();
  if (!force && !inHours(now, open || DEFAULT_OPEN, close || DEFAULT_CLOSE)) return { ok: true, skipped: 'outside restaurant hours' };

  const range = businessRange(now);
  const tabId = await helperTab();
  const parts = [await petpoojaPart(tabId, code, range)];    // each part is independent: one failing doesn't stop the other
  if (zomato !== false) parts.push(await zomatoPart(tabId, code, range));
  const ok = parts.every((p) => p.ok);
  const msg = parts.map((p) => p.msg).join(' · ');
  const dishes = parts.reduce((s, p) => s + (p.dishes || 0), 0);
  await save({ lastAt: Date.now(), lastOk: ok, lastMsg: msg, lastDishes: dishes });
  setBadge(ok);
  return { ok, msg, error: ok ? undefined : msg, dishes, parts };
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
  if (msg && msg.cmd === 'hours') {
    load(['open', 'close', 'zomato']).then((s) => sendResponse({ open: s.open || DEFAULT_OPEN, close: s.close || DEFAULT_CLOSE, zomato: s.zomato !== false }));
    return true;
  }
});
