// Runs on Petpooja's Online Menu "item on/off" page (the one switch for Ownly, Zomato and Swiggy). When
// background.js asks, it puts the named items ON or OFF by pressing the row's own ON / OFF button, exactly
// as a person would. For OFF it fills Petpooja's side panel (4 Hours as a safety net, all platforms) and
// presses Save Changes.
(() => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const btn = (tr, word) => [...tr.querySelectorAll('button, a, label')].find((b) => norm(b.textContent) === word);
  const stateOf = (tr) => {       // the selected button is the one WITHOUT Bootstrap's grey "btn-secondary"
    const on = btn(tr, 'on'), off = btn(tr, 'off');
    if (!on || !off) return null;
    const grey = (b) => /btn-secondary/.test(b.className || '');
    if (grey(off) && !grey(on)) return 'on';
    if (grey(on) && !grey(off)) return 'off';
    return null;
  };
  const visible = (e) => e && e.offsetParent !== null;
  // Turning an item OFF opens a side panel: how long, and which platforms (all ticked by default), then Save Changes.
  async function saveOffPanel(hours) {
    let save = null;
    for (let i = 0; i < 12 && !save; i++) {
      save = [...document.querySelectorAll('button')].find((b) => visible(b) && norm(b.textContent) === 'save changes');
      if (!save) await sleep(500);
    }
    if (!save) return 'the off panel did not open';
    const lab = [...document.querySelectorAll('label')].find((l) => visible(l) && norm(l.textContent) === hours);
    const inp = lab && (lab.control || lab.querySelector('input'));
    if (!inp) return `no "${hours}" option in the off panel`;
    inp.click();
    await sleep(400);
    const boxes = [...document.querySelectorAll('input[type=checkbox]')].filter((i) => visible(i.closest('label') || i));
    for (const bx of boxes) { if (!bx.checked && /^(all|swiggy|zomato|ownly)/i.test(norm((bx.closest('label') || bx.parentElement).textContent))) bx.click(); }
    save.click();
    await sleep(3000);
    return [...document.querySelectorAll('button')].some((b) => visible(b) && norm(b.textContent) === 'save changes') ? 'the off panel stayed open after Save' : null;
  }
  async function switchItems(names, want) {
    for (let i = 0; i < 40 && !document.querySelector('tbody tr'); i++) await sleep(500);
    const res = [];
    for (const name of names) {
      const find = () => [...document.querySelectorAll('tr')].find((r) => [...r.children].some((td) => norm(td.textContent) === norm(name)));
      const tr = find();
      if (!tr) { res.push({ name, ok: false, error: 'not found on the page' }); continue; }
      const now = stateOf(tr);
      if (now === want) { res.push({ name, ok: true, already: true }); continue; }
      const b = btn(tr, want);
      if (!b) { res.push({ name, ok: false, error: 'no ' + want + ' button' }); continue; }
      b.click();
      await sleep(2500);
      if (want === 'off') {
        const err = await saveOffPanel('4 hours');
        if (err) { res.push({ name, ok: false, error: err }); break; }
      }
      const after = find() && stateOf(find());
      res.push({ name, ok: after === want, error: after === want ? undefined : 'the button did not change' });
    }
    return { ok: res.every((r) => r.ok), res, error: res.filter((r) => !r.ok).map((r) => r.name + ': ' + r.error).join('; ') || undefined };
  }
  chrome.runtime.onMessage.addListener((msg, _s, sendResponse) => {
    if (!msg || msg.cmd !== 'switch') return;
    switchItems(msg.names || [], msg.want).then(sendResponse, (e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  });
})();
