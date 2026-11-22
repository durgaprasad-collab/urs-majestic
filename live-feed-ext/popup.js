const $ = (id) => document.getElementById(id);
const st = $('st');
const show = (txt, cls = '') => { st.textContent = txt; st.className = cls; };
const ago = (t) => { const m = Math.round((Date.now() - t) / 60000); return m < 1 ? 'just now' : m + ' min ago'; };
const clock = (hhmm) => { const [h, m] = hhmm.split(':').map(Number); return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h >= 12 ? 'PM' : 'AM'}`; };

async function status() {
  const hrs = await chrome.runtime.sendMessage({ cmd: 'hours' });
  $('open').value = hrs.open;
  $('close').value = hrs.close;
  $('tandoori').checked = hrs.tandoori;
  $('final').checked = hrs.final;
  const s = await chrome.storage.local.get(['code', 'lastAt', 'lastOk', 'lastMsg', 'finalAt', 'finalOk', 'finalMsg', 'tNote', 'tOk']);
  const nightly = s.finalAt ? `
${s.finalOk ? '✓' : '⚠'} ${s.finalMsg} · ${ago(s.finalAt)}` : '';
  const tnd = s.tNote ? `
${s.tOk ? '✓' : '⚠'} ${s.tNote}` : '';
  const when = `Runs ${clock(hrs.open)} to ${clock(hrs.close)}.`;
  if (!s.code) return show('Paste your helper code, then press Save & test. ' + when);
  $('code').placeholder = 'Code saved (hidden)';
  if (!s.lastAt) return show(`Code saved. ${when} The first read happens within a few minutes of opening.`);
  show(`${s.lastOk ? '✓' : '⚠'} ${s.lastMsg} · ${ago(s.lastAt)}. ${when}${nightly}${tnd}`, s.lastOk && s.finalOk !== false ? 'ok' : 'bad');
}

$('save').addEventListener('click', async () => {
  const code = $('code').value.trim();
  const save = { open: $('open').value || '12:00', close: $('close').value || '01:00', tandoori: $('tandoori').checked, final: $('final').checked };
  if (code) save.code = code;
  await chrome.storage.local.set(save);
  show('Testing…');
  const r = await chrome.runtime.sendMessage({ cmd: 'test' });
  show(r && r.ok ? `✓ Connected to admin.ursmajestic.com. Hours: ${clock(save.open)} to ${clock(save.close)}` : '⚠ ' + ((r && r.error) || 'Test failed'), r && r.ok ? 'ok' : 'bad');
  $('code').value = '';
});

$('run').addEventListener('click', async () => {
  show('Reading Petpooja… this takes about half a minute. You can close this window.');
  try {
    const r = await chrome.runtime.sendMessage({ cmd: 'run-now' });
    if (r && r.ok) show('✓ ' + (r.msg || 'Sent'), 'ok');
    else show('⚠ ' + ((r && (r.msg || r.error)) || 'Failed'), 'bad');
  } catch (e) {
    show('The add-on was interrupted. Open this window again in a minute to see the result.', 'bad');
  }
});

$('fin').addEventListener('click', async () => {
  show('Uploading the finished day… this takes about a minute. You can close this window.');
  try {
    const r = await chrome.runtime.sendMessage({ cmd: 'final-now' });
    if (r && r.skipped) show('Nothing to upload right now: yesterday is already uploaded' + (r.skipped === true ? '.' : ' (' + r.skipped + ').'), 'ok');
    else if (r && r.ok) show('✓ ' + (r.msg || 'Uploaded'), 'ok');
    else show('⚠ ' + ((r && (r.msg || r.error)) || 'Failed'), 'bad');
  } catch (e) {
    show('The add-on was interrupted. Open this window again in a minute to see the result.', 'bad');
  }
});

$('tnd').addEventListener('click', async () => {
  show('Checking the tandoori items…');
  await chrome.runtime.sendMessage({ cmd: 'tandoori-now' });
  status();
});

status();
