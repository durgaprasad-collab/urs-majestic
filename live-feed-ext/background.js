// URS Live Stock Feed: every ~10 minutes, during the restaurant's hours only:
//   1. press Refresh on Petpooja's home page (so its reports aren't behind),
//   2. open Petpooja's Item Report (Day Wise With Time) for today's business day (1 AM to
//      1 AM), read its rows (every item, from every channel) and send them to the Live stock tab,
//   3. open Petpooja's All Orders page and send today's orders so the server can split sales by
//      source (Zomato / Swiggy / Ownly / walk-in) from each order's type tag.
// After closing it also uploads the finished day's item report and order listing (about 1:15 AM,
// again about 6 AM). Zomato's and Swiggy's own portals are no longer read.
// Every day from 3:30 PM to 6:30 PM it switches the tandoori items OFF in Petpooja's Online Menu
// (which controls Ownly, Zomato and Swiggy) and back ON at 6:30 PM.
const BASE = 'https://admin.ursmajestic.com';
const REPORT_URL = 'https://billing.petpooja.com/custom_reports/view_report/55';
const REPORT_PATTERN = 'https://billing.petpooja.com/custom_reports/view_report/*';
const ORDERS_URL = 'https://billing.petpooja.com/orders/order_list/';
const DASHBOARD_URL = 'https://billing.petpooja.com/users/dashboard';
const DASHBOARD_PATTERN = 'https://billing.petpooja.com/users/dashboard*';
const MENU_URL = 'https://menu.petpooja.com/menus/item_on_off/';
const TANDOORI = ['Roti (2 Nos)', 'Butter Roti (2 Nos)', 'Naan (1 No)', 'Butter Naan (1 No)', 'Garlic Naan (1 No)', 'Masala Kulcha (1 No)', 'Paneer Power Combo (02)'];
const TANDOORI_OFF = '15:30';   // switch off from here...
const TANDOORI_ON = '18:30';    // ...until here, PC's local time
const FINAL_FIRST = '01:15';   // first upload of the finished day, PC's local time
const FINAL_AGAIN = '06:00';  // second, once Petpooja has synced its late sales; replaces the first
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

// The helper uses its own pinned tab and remembers it, so it never takes over a Petpooja or
// Zomato tab you have open yourself.
async function helperTab() {
  const { helperTabId } = await load(['helperTabId']);
  if (helperTabId) {
    try { await chrome.tabs.get(helperTabId); return helperTabId; } catch (_) { /* closed: make a new one */ }
  }
  const tab = await chrome.tabs.create({ url: DASHBOARD_URL, active: false, pinned: true });
  await save({ helperTabId: tab.id });
  await waitComplete(tab.id);
  return tab.id;
}

// Ask the page's script for something; it may need a moment to come up after a load.
// Returns its answer, or {ok:false, error} saying what was wrong (and which page the tab was on).
async function ask(tabId, message, tries = 4) {
  let why = '';
  for (let i = 0; i < tries; i++) {
    try {
      const res = await chrome.tabs.sendMessage(tabId, message);
      if (res) return res;
      why = 'the page script gave no answer';
    } catch (e) { why = String((e && e.message) || e).slice(0, 90); }
    await sleep(3000);
  }
  let where = '';
  try { const t = await chrome.tabs.get(tabId); where = ` The tab was on ${String(t.url || '').split('?')[0].slice(0, 90)}.`; } catch (_) { /* tab gone */ }
  return { ok: false, error: `no answer from the page (${why}).${where}` };
}

// Chrome stops an add-on's background worker after ~30 s without activity; a read takes longer than
// that, so poke an extension API while it runs.
function keepAlive() {
  const t = setInterval(() => { try { chrome.runtime.getPlatformInfo(() => {}); } catch (_) { /* ignore */ } }, 20000);
  return () => clearInterval(t);
}

async function progress(msg) { await save({ lastAt: Date.now(), lastOk: true, lastMsg: msg }); }

async function post(code, body, path = '/api/live-feed') {
  let resp;
  try {
    resp = await fetch(`${BASE}${path}`, {
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

// Read the item report for a range. If it comes back showing another day (the date picker or
// table didn't take the new range), reload the report once and try again before giving up.
async function readReport(tabId, range) {
  let res = await ask(tabId, { cmd: 'read', range });
  if (!res.ok && /showing another day/.test(res.error || '')) {
    await sleep(2000);
    await goto(tabId, REPORT_URL);
    res = await ask(tabId, { cmd: 'read', range });
  }
  return res;
}

// Petpooja walk-in sales: Refresh on the home page, then the item report.
async function petpoojaPart(tabId, code, range) {
  await goto(tabId, DASHBOARD_URL);
  const refreshed = await ask(tabId, { cmd: 'refresh' }, 3);
  await goto(tabId, REPORT_URL);
  const res = await readReport(tabId, range);
  if (!res.ok) return { ok: false, msg: `Petpooja: ${res.error || 'read failed'}` };
  if (!res.rows || !res.rows.length) return { ok: true, empty: true, msg: 'Petpooja: no sales yet' };
  const sent = await post(code, { channel: 'petpooja', rows: res.rows });
  if (!sent.ok) return { ok: false, msg: `Petpooja: ${sent.error}` };
  return { ok: true, rows: sent.rows, dishes: sent.dishes, msg: `Petpooja: ${sent.rows} items, ${Math.round(sent.dishes)} dishes${refreshed.ok ? '' : ` (${refreshed.error})`}` };
}

// Today's All Orders list, for the source split (Zomato / Swiggy / Ownly / walk-in). Reads the same
// page as the nightly Order Listing, for the current business day.
async function sourcesPart(tabId, code, range) {
  await goto(tabId, ORDERS_URL);
  const res = await ask(tabId, { cmd: 'olread', day: range.day, live: true }, 3);
  if (!res.ok) return { ok: false, msg: `Orders: ${res.error || 'read failed'}` };
  const sent = await post(code, { channel: 'sources', rows: res.rows });
  if (!sent.ok) return { ok: false, msg: `Orders: ${sent.error}` };
  return { ok: true, msg: `Orders: ${sent.orders} (${(sent.split || []).map((x) => `${x.source} ${x.orders}`).join(', ')})` };
}

// The finished business day's Order Listing (Petpooja's All Orders page), replacing the manual Order Listing upload.
async function ordersFinalPart(tabId, code, day) {
  await goto(tabId, ORDERS_URL);
  const res = await ask(tabId, { cmd: 'olread', day }, 3);
  if (!res.ok) return { ok: false, msg: `Orders ${day}: ${res.error || 'read failed'}` };
  const sent = await post(code, { channel: 'petpooja_orders', day, rows: res.rows }, '/api/live-feed/final');
  if (!sent.ok) return { ok: false, msg: `Orders ${day}: ${sent.error}` };
  return { ok: true, msg: `Orders ${day}: ${sent.orders} orders${sent.alarm ? ` (${sent.alarm})` : ''}` };
}

// The finished business day's item report, sent once the day has closed. Safe to repeat: the
// server replaces that day's rows with the latest read.
async function finalPart(tabId, code, day) {
  const next = new Date(day + 'T12:00:00'); next.setDate(next.getDate() + 1);
  const range = { day, from: `${day} 01:00:00`, to: `${ymd(next)} 01:00:00` };
  await goto(tabId, DASHBOARD_URL);
  await ask(tabId, { cmd: 'refresh' }, 3);      // so the report isn't behind the till
  await goto(tabId, REPORT_URL);
  const res = await readReport(tabId, range);
  if (!res.ok) return { ok: false, msg: `${day}: ${res.error || 'read failed'}` };
  if (!res.rows || !res.rows.length) return { ok: false, msg: `${day}: the report had no sales` };
  const sent = await post(code, { channel: 'petpooja', day, rows: res.rows }, '/api/live-feed/final');
  if (!sent.ok) return { ok: false, msg: `${day}: ${sent.error}` };
  return { ok: true, msg: `${day}: ${sent.rows} items, ₹${Math.round(sent.amount).toLocaleString('en-IN')}${sent.unmatched && sent.unmatched.length ? `, ${sent.unmatched.length} not matched to the menu` : ''}` };
}

// Which finished days still need an upload: yesterday (twice: after 1:15 AM, then after 6 AM),
// and any of the three days before it that were never sent (PC was off).
function finalsDue(now, finals) {
  const m = now.getHours() * 60 + now.getMinutes();
  if (m < toMins(FINAL_FIRST)) return [];
  const want = m >= toMins(FINAL_AGAIN) ? 2 : 1;
  const due = [];
  for (let back = 1; back <= 4; back++) {
    const d = new Date(now); d.setDate(d.getDate() - back);
    const day = ymd(d), have = finals[day] || 0;
    if (have < (back === 1 ? want : 1)) due.push({ day, stage: back === 1 ? want : 1 });
  }
  return due.reverse();   // oldest first
}

async function runFinal(force = false) {
  const { code, final, finals, finalsO } = await load(['code', 'final', 'finals', 'finalsO']);
  if (!code || final === false || running) return { ok: true, skipped: true };
  const now = new Date();
  const due = finalsDue(now, finals || {});
  const dueO = finalsDue(now, finalsO || {});
  if (!due.length && !dueO.length) return { ok: true, skipped: 'nothing due' };
  running = true;
  const stop = keepAlive();
  try {
    const tabId = await helperTab();
    const done = { ...(finals || {}) }, doneO = { ...(finalsO || {}) }, notes = [];
    let ok = true;
    for (const { day, stage } of due) {          // each source is independent: one failing doesn't stop the other
      const r = await finalPart(tabId, code, day);
      notes.push(r.msg);
      if (r.ok) done[day] = stage; else ok = false;
    }
    for (const { day, stage } of dueO) {
      const r = await ordersFinalPart(tabId, code, day);
      notes.push(r.msg);
      if (r.ok) doneO[day] = stage; else ok = false;
    }
    const trim = (m) => { const keep = Object.keys(m).sort().slice(-10); return Object.fromEntries(keep.map((k) => [k, m[k]])); };
    await save({ finals: trim(done), finalsO: trim(doneO), finalAt: Date.now(), finalOk: ok, finalMsg: `Nightly upload: ${notes.join(' · ')}` });
    return { ok, msg: notes.join(' · ') };
  } catch (e) {
    await save({ finalAt: Date.now(), finalOk: false, finalMsg: `Nightly upload stopped: ${String((e && e.message) || e).slice(0, 120)}` });
    return { ok: false };
  } finally {
    running = false;
    stop();
  }
}

let running = false;

// Tandoori items: OFF from 3:30 PM to 6:30 PM, ON otherwise. Acts only when the wanted state differs from
// what it last set, so it never switches anything on that it did not switch off itself.
async function tandooriTick() {
  const { tandoori, tState, tTry } = await load(['tandoori', 'tState', 'tTry']);
  if (tandoori === false || running) return;
  const now = new Date(), m = now.getHours() * 60 + now.getMinutes();
  const want = (m >= toMins(TANDOORI_OFF) && m < toMins(TANDOORI_ON)) ? 'off' : 'on';
  if (want === (tState || 'on')) return;
  if (tTry && Date.now() - tTry < 4 * 60000) return;   // after a failure, wait a few minutes
  running = true;
  const stop = keepAlive();
  try {
    await save({ tTry: Date.now() });
    const tabId = await helperTab();
    await goto(tabId, MENU_URL);
    const r = await ask(tabId, { cmd: 'switch', want, names: TANDOORI }, 3);
    const when = now.toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' });
    if (r.ok) await save({ tState: want, tNote: `Tandoori items switched ${want.toUpperCase()} at ${when}`, tOk: true });
    else await save({ tNote: `Tandoori ${want.toUpperCase()} at ${when} failed: ${r.error || 'unknown'}`, tOk: false });
  } catch (e) {
    await save({ tNote: `Tandoori switch stopped: ${String((e && e.message) || e).slice(0, 120)}`, tOk: false });
  } finally {
    running = false;
    stop();
  }
}

async function run(force = false) {
  const { code, open, close } = await load(['code', 'open', 'close']);
  if (!code) return fail('No helper code saved yet');
  const now = new Date();
  if (!force && !inHours(now, open || DEFAULT_OPEN, close || DEFAULT_CLOSE)) return { ok: true, skipped: 'outside restaurant hours' };
  if (running) return { ok: false, error: 'A read is already running. Give it a minute or two.', msg: 'A read is already running. Give it a minute or two.' };

  running = true;
  const stop = keepAlive();
  try {
    const range = businessRange(now);
    const tabId = await helperTab();
    await progress('Reading Petpooja…');
    const parts = [await petpoojaPart(tabId, code, range)];    // each part is independent: one failing doesn't stop the other
    await progress(parts[0].msg + ' · reading All Orders…');
    parts.push(await sourcesPart(tabId, code, range));
    const ok = parts.every((p) => p.ok);
    const msg = parts.map((p) => p.msg).join(' · ');
    const dishes = parts.reduce((s, p) => s + (p.dishes || 0), 0);
    await save({ lastAt: Date.now(), lastOk: ok, lastMsg: msg, lastDishes: dishes });
    setBadge(ok);
    return { ok, msg, error: ok ? undefined : msg, dishes, parts };
  } catch (e) {
    return fail(`Stopped by an error: ${String((e && e.message) || e).slice(0, 120)}`);
  } finally {
    running = false;
    stop();
  }
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

function schedule() {
  chrome.alarms.create('urs-live', { delayInMinutes: 1, periodInMinutes: 10 });
  chrome.alarms.create('urs-final', { delayInMinutes: 2, periodInMinutes: 15 });
  chrome.alarms.create('urs-tandoori', { delayInMinutes: 1, periodInMinutes: 1 });
}
chrome.runtime.onInstalled.addListener(schedule);
chrome.runtime.onStartup.addListener(schedule);
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === 'urs-live') run(false);
  if (a.name === 'urs-final') runFinal(false);
  if (a.name === 'urs-tandoori') tandooriTick();
});

chrome.runtime.onMessage.addListener((msg, _s, sendResponse) => {
  if (msg && msg.cmd === 'run-now') { run(true).then(sendResponse); return true; }
  if (msg && msg.cmd === 'final-now') { runFinal(true).then(sendResponse); return true; }
  if (msg && msg.cmd === 'tandoori-now') { save({ tTry: 0 }).then(tandooriTick).then(() => sendResponse({ ok: true })); return true; }
  if (msg && msg.cmd === 'test') { ping().then(sendResponse); return true; }
  if (msg && msg.cmd === 'hours') {
    load(['open', 'close', 'final', 'tandoori', 'tandoriNote']).then((s) => sendResponse({ open: s.open || DEFAULT_OPEN, close: s.close || DEFAULT_CLOSE, final: s.final !== false, tandoori: s.tandoori !== false }));
    return true;
  }
});
