const $ = (id) => document.getElementById(id);
const st = $('st');
const show = (txt, cls = '') => { st.textContent = txt; st.className = cls; };
const ago = (t) => { const m = Math.round((Date.now() - t) / 60000); return m < 1 ? 'just now' : m + ' min ago'; };

async function status() {
  const s = await chrome.storage.local.get(['code', 'lastAt', 'lastOk', 'lastMsg']);
  if (!s.code) return show('Paste your helper code, then press Save & test.');
  $('code').placeholder = 'Code saved (hidden)';
  if (!s.lastAt) return show('Code saved. The first read happens within a minute or so (10:30 AM to midnight).');
  show(`${s.lastOk ? '✓' : '⚠'} ${s.lastMsg} · ${ago(s.lastAt)}`, s.lastOk ? 'ok' : 'bad');
}

$('save').addEventListener('click', async () => {
  const code = $('code').value.trim();
  if (code) await chrome.storage.local.set({ code });
  show('Testing…');
  const r = await chrome.runtime.sendMessage({ cmd: 'test' });
  show(r && r.ok ? '✓ Connected to admin.ursmajestic.com' : '⚠ ' + ((r && r.error) || 'Test failed'), r && r.ok ? 'ok' : 'bad');
  $('code').value = '';
});

$('run').addEventListener('click', async () => {
  show('Reading Petpooja…');
  const r = await chrome.runtime.sendMessage({ cmd: 'run-now' });
  if (r && r.ok) show(r.empty ? 'No sales in the report yet' : `✓ Sent ${r.rows} items, ${Math.round(r.dishes)} dishes`, 'ok');
  else show('⚠ ' + ((r && r.error) || 'Failed'), 'bad');
});

status();
