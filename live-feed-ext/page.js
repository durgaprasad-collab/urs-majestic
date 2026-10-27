// Runs inside the Petpooja page itself (MAIN world) so it can read the report's
// own DataTable. On request it sets the report's date range to the business day
// (1 AM to 1 AM), presses the report's own Search button and reads the rows.
// It never sends anything anywhere; reading is all it does.
(() => {
  if (window.__ursLiveFeed) return;
  window.__ursLiveFeed = true;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

  const forDay = (rows, day) => rows.every((r) => String(r[1]).startsWith(day));

  const read = async (range) => {
    const day = range && range.day;
    if (range) {
      const from = document.querySelector('input.start_fromdate'), to = document.querySelector('input.end_todate');
      const search = document.querySelector('.re_final_search');
      if (from && to && search) {
        const same = from.value === range.from && to.value === range.to;
        from.value = range.from;
        to.value = range.to;
        if (!same || !tableRows()?.length) { search.click(); await sleep(2500); }
      }
    }
    // The report fills in a moment after it loads or is searched.
    for (let i = 0; i < 40; i++) {
      const rows = tableRows();
      if (rows && rows.length && (!day || forDay(rows, day))) {
        return { ok: true, rows: rows.map((r) => [String(r[0]).trim(), String(r[1]), String(r[2]), String(r[3])]) };
      }
      await sleep(500);
    }
    const rows = tableRows();
    if (rows && !rows.length && window.jQuery) return { ok: true, rows: [], note: 'No sales in the report yet' };
    if (rows && rows.length) return { ok: false, error: 'The report is showing another day, not ' + day };
    return { ok: false, error: 'Report table not found. Is this the Item Report page?' };
  };

  window.addEventListener('message', async (ev) => {
    if (ev.source !== window || !ev.data || ev.data.type !== 'urs-live-read') return;
    const result = await read(ev.data.range);
    window.postMessage({ type: 'urs-live-data', id: ev.data.id, ...result }, '*');
  });
})();
