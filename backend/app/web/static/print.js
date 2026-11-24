// Android app webview has a print bridge; elsewhere window.print (iOS home-screen apps may ignore it: use Share list).
document.querySelectorAll('[data-print]').forEach(b => b.addEventListener('click', () =>
  (window.URSPrint && window.URSPrint.print) ? window.URSPrint.print(document.title) : window.print()));
// Share the list as plain text (WhatsApp, Notes, print from there) where the phone supports it, else copy it.
document.querySelectorAll('[data-share]').forEach(b => b.addEventListener('click', async () => {
  let text = 'URS Majestic · order for the week\n';
  document.querySelectorAll('h2').forEach(h => {
    text += `\n${h.textContent.trim()}\n`;
    h.nextElementSibling.querySelectorAll('tr').forEach(tr => {
      const td = tr.querySelectorAll('td');
      const c = td[1].cloneNode(true);
      c.querySelectorAll('.px').forEach(x => x.remove());   // share the item and quantity only, not the price notes
      text += `• ${c.textContent.trim()} – ${td[2].textContent.trim()}\n`;
    });
  });
  try {
    if (navigator.share) await navigator.share({ text });
    else { await navigator.clipboard.writeText(text); alert('List copied. Paste it in WhatsApp or Notes.'); }
  } catch (e) { /* share sheet dismissed */ }
}));
