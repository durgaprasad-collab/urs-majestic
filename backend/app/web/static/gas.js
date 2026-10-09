// Gas page: show/hide the add form, one-step correction of a reading.
(function () {
  const add = document.querySelector('[data-gs-add]');
  const form = document.querySelector('[data-gs-form]');
  if (add && form) add.addEventListener('click', () => { form.hidden = !form.hidden; if (!form.hidden) form.querySelector('[name=gross_kg]').focus(); });

  async function save(input) {
    const v = input.value.trim();
    if (v === input.dataset.orig || input.dataset.busy) return;
    input.dataset.busy = '1';
    try {
      const res = await fetch(`/gas-log/reading/${input.dataset.gsEdit}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ gross_kg: v }),
      });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) { alert(j.error || 'Not saved'); input.value = input.dataset.orig; return; }
      input.dataset.orig = v;
      const ok = input.parentNode.querySelector('[data-gs-ok]');
      if (ok) { ok.hidden = false; setTimeout(() => location.reload(), 600); }
    } finally { delete input.dataset.busy; }
  }
  document.querySelectorAll('[data-gs-edit]').forEach(input => {
    input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); save(input); } });
    input.addEventListener('blur', () => save(input));
  });
})();
