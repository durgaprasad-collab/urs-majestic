const $ = (id) => document.getElementById(id);
const st = $('st');
const show = (txt, cls = '') => { st.textContent = txt; st.className = cls; };
const ago = (t) => { const m = Math.round((Date.now() - t) / 60000); return m < 1 ? 'just now' : m + ' min ago'; };
const clock = (hhmm) => { const [h, m] = hhmm.split(':').map(Number); return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h >= 12 ? 'PM' : 'AM'}`; };

async function status() {
  const hrs = await chrome.runtime.sendMessage({ cmd: 'hours' });
  $('open').value = hrs.open;
  $('close').value = hrs.close;
  $('zomato').checked = hrs.zomato;
  const s = await chrome.storage.local.get(['code', 'lastAt', 'lastOk', 'lastMsg']);
  const when = `Runs ${clock(hrs.open)} to ${clock(hrs.close)}.`;
  if (!s.code) return show('Paste your helper code, then press Save & test. ' + when);
  $('code').placeholder = 'Code saved (hidden)';
  if (!s.lastAt) return show(`Code saved. ${when} The first read happens within a few minutes of opening.`);
  show(`${s.lastOk ? '✓' : '⚠'} ${s.lastMsg} · ${ago(s.lastAt)}. ${when}`, s.lastOk ? 'ok' : 'bad');
}

$('save').addEventListener('click', async () => {
  const code = $('code').value.trim();
  const save = { open: $('open').value || '12:00', close: $('close').value || '01:00', zomato: $('zomato').checked };
  if (code) save.code = code;
  await chrome.storage.local.set(save);
  show('Testing…');
  const r = await chrome.runtime.sendMessage({ cmd: 'test' });
  show(r && r.ok ? `✓ Connected to admin.ursmajestic.com. Hours: ${clock(save.open)} to ${clock(save.close)}${save.zomato ? ', with Zomato' : ', Petpooja only'}` : '⚠ ' + ((r && r.error) || 'Test failed'), r && r.ok ? 'ok' : 'bad');
  $('code').value = '';
});

$('run').addEventListener('click', async () => {
  show('Reading Petpooja' + ($('zomato').checked ? ' and Zomato' : '') + '… this takes about a minute and a half. You can close this window.');
  try {
    const r = await chrome.runtime.sendMessage({ cmd: 'run-now' });
    if (r && r.ok) show('✓ ' + (r.msg || 'Sent'), 'ok');
    else show('⚠ ' + ((r && (r.msg || r.error)) || 'Failed'), 'bad');
  } catch (e) {
    show('The add-on was interrupted. Open this window again in a minute to see the result.', 'bad');
  }
});

status();
