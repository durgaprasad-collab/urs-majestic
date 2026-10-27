// Runs inside the Petpooja page itself (MAIN world) so it can read the report's
// own DataTable. It only answers a request from content.js and never sends
// anything anywhere; reading is all it does.
(() => {
  if (window.__ursLiveFeed) return;
  window.__ursLiveFeed = true;

  const tableRows = () => {
    const $ = window.jQuery;
    if (!$ || !$.fn || !$.fn.dataTable) return null;
    for (const el of $.fn.dataTable.tables()) {
      const rows = $(el).DataTable().rows({ search: 'none' }).data().toArray();
      // Item report rows look like [name, date, qty, total]
      if (rows.length && Array.isArray(rows[0]) && rows[0].length >= 4) return rows;
    }
    return [];
  };

  const read = async () => {
    // The report fills in a moment after the page loads.
    for (let i = 0; i < 30; i++) {
      const rows = tableRows();
      if (rows && rows.length) {
        return { ok: true, rows: rows.map((r) => [String(r[0]).trim(), String(r[1]), String(r[2]), String(r[3])]) };
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    const rows = tableRows();
    if (rows && !rows.length && window.jQuery) return { ok: true, rows: [], note: 'No sales in the report yet' };
    return { ok: false, error: 'Report table not found. Is this the Item Report: Day Wise page?' };
  };

  window.addEventListener('message', async (ev) => {
    if (ev.source !== window || !ev.data || ev.data.type !== 'urs-live-read') return;
    const result = await read();
    window.postMessage({ type: 'urs-live-data', id: ev.data.id, ...result }, '*');
  });
})();
