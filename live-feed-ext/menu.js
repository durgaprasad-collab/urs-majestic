// Runs on Petpooja's Online Menu "item on/off" page (the one switch for Ownly, Zomato and Swiggy). When
// background.js asks, it puts the named items ON or OFF by pressing the row's own ON / OFF button, exactly
// as a person would. If Petpooja asks anything first (a dialog), it does NOT answer it: it reports the
// dialog's text so the add-on can be taught what to do.
(() => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const btn = (tr, word) => [...tr.querySelectorAll('button, a, label')].find((b) => norm(b.textContent) === word);
  const stateOf = (tr) => {
    const on = btn(tr, 'on'), off = btn(tr, 'off');
    if (!on || !off) return null;
    const dim = (b) => /inactive|disabled/.test(b.className || '');
    if (dim(off) && !dim(on)) return 'on';
    if (dim(on) && !dim(off)) return 'off';
    return null;
  };
  const dialog = () => {
    const m = [...document.querySelectorAll('.modal, .swal2-container, [role=dialog], .bootbox')].find((e) => e.offsetParent !== null && norm(e.textContent));
    return m ? norm(m.textContent).slice(0, 300) : null;
  };
  async function switchItems(names, want) {
    for (let i = 0; i < 40 && !document.querySelector('tbody tr'); i++) await sleep(500);
    const rows = [...document.querySelectorAll('tr')];
    const res = [];
    for (const name of names) {
      const tr = rows.find((r) => [...r.children].some((td) => norm(td.textContent) === norm(name)));
      if (!tr) { res.push({ name, ok: false, error: 'not found on the page' }); continue; }
      const now = stateOf(tr);
      if (now === want) { res.push({ name, ok: true, already: true }); continue; }
      const b = btn(tr, want);
      if (!b) { res.push({ name, ok: false, error: 'no ' + want + ' button' }); continue; }
      b.click();
      await sleep(2500);
      const d = dialog();
      if (d) { res.push({ name, ok: false, error: 'Petpooja asked: ' + d }); break; }   // stop: don't guess at an unknown dialog
      res.push({ name, ok: stateOf(tr) === want, error: stateOf(tr) === want ? undefined : 'the button did not change' });
    }
    return { ok: res.every((r) => r.ok), res, error: res.filter((r) => !r.ok).map((r) => r.name + ': ' + r.error).join('; ') || undefined };
  }
  chrome.runtime.onMessage.addListener((msg, _s, sendResponse) => {
    if (!msg || msg.cmd !== 'switch') return;
    switchItems(msg.names || [], msg.want).then(sendResponse, (e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  });
})();
