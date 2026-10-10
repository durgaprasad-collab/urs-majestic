// Recipes (templates/recipes.html): dish recipe cards with per-dish grams,
// ingredient view (where used, recipe use vs bought), and the attention list.
(function () {
  const D = JSON.parse(document.getElementById('rp-data').textContent);
  const SEL = JSON.parse(document.getElementById('rp-sel').textContent) || {};
  const rs = (n, d = 0) => '₹' + Number(n).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
  const esc = t => String(t ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fcc = f => (f > 45 ? 'hi' : f > 35 ? 'mid' : 'ok');
  const $ = s => document.querySelector(s);
  const byId = Object.fromEntries(D.dishes.map(d => [d.id, d]));
  const ingById = Object.fromEntries(D.ings.map(i => [i.id, i]));
  const plates = d => d.q + d.qd;
  const priced = l => l.role === 'recipe' && l.cat !== 'Spices';
  const lineCost = l => (priced(l) && l.g != null && l.kg != null ? l.g * l.kg / 1000 : null);
  const recipeCost = id => (D.lines[id] || []).reduce((s, l) => s + (lineCost(l) || 0), 0);
  const dishCost = d => d.combo
    ? (D.combos[d.id] || []).reduce((s, c) => s + (c.id ? c.pf * recipeCost(c.id) : 0) + (c.fc || 0), 0)
    : recipeCost(d.id);
  const fullCost = d => dishCost(d) + D.spice + d.pack;
  const fc = d => fullCost(d) / d.p * 100;
  async function post(url, body) {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(body || {}) });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.error || 'Not saved');
    return j;
  }
  const reload = q => { location.href = '/ingredients/mapping?' + new URLSearchParams(q).toString(); };

  // ── Findings
  const all = D.dishes.flatMap(d => (D.lines[d.id] || []).map(l => ({ d, l })));
  const conflicts = all.filter(x => x.l.conflict);
  const weighed = all.filter(x => x.l.src === 'weighed').length;
  const estDishes = D.dishes.filter(d => !d.combo && (D.lines[d.id] || []).some(l => l.src === 'estimate' && priced(l)));
  const noRecipe = D.ings.filter(i => i.role === 'recipe' && i.nd === 0);
  const unpriced = D.ings.filter(i => i.role === 'recipe' && i.nd > 0 && i.kg == null && i.c !== 'Spices');
  const pw = D.dishes.reduce((s, d) => s + plates(d), 0);
  const avg = pw ? D.dishes.reduce((s, d) => s + fc(d) * plates(d), 0) / pw : 0;
  const attN = (conflicts.length ? 1 : 0) + (noRecipe.length ? 1 : 0) + (unpriced.length ? 1 : 0) + (estDishes.length ? 1 : 0);

  $('[data-rp-kpis]').innerHTML = `
    <button type="button" class="sl-k good" data-go="dish"><span class="l">Dishes costed</span><span class="v">${D.dishes.filter(d => d.conf === 'reliable').length} <small>of ${D.dishes.length}</small></span><span class="s">${D.dishes.filter(d => d.conf !== 'reliable').length} provisional · ${Object.keys(D.combos).length} combos built from dishes</span></button>
    <button type="button" class="sl-k ${weighed < all.length ? 'warn' : 'good'}" data-go="att"><span class="l">Grams confirmed</span><span class="v">${weighed} <small>of ${all.length} lines</small></span><span class="s">${all.length - weighed} still estimates from the old light / medium / heavy</span></button>
    <button type="button" class="sl-k ${avg > 40 ? 'warn' : 'good'}" data-go="dish"><span class="l">Food cost</span><span class="v">${avg.toFixed(1)}%</span><span class="s">plate-weighted, last 30 days' plates</span></button>
    <button type="button" class="sl-k ${conflicts.length ? 'bad' : 'good'}" data-go="att"><span class="l">Two weights saved</span><span class="v">${conflicts.length}</span><span class="s">${conflicts.length ? 'same dish, same ingredient: pick one' : 'every line has one weight'}</span></button>
    <button type="button" class="sl-k ${noRecipe.length ? 'warn' : 'good'}" data-go="att"><span class="l">In no recipe</span><span class="v">${noRecipe.length}</span><span class="s">kitchen items no dish uses</span></button>`;
  $('[data-rp-attn]').textContent = attN || '';

  function tab(t) {
    document.querySelectorAll('[data-rp-tab]').forEach(b => b.classList.toggle('on', b.dataset.rpTab === t));
    document.querySelectorAll('[data-rp-panel]').forEach(p => { p.hidden = p.dataset.rpPanel !== t; });
  }
  document.querySelectorAll('[data-rp-tab]').forEach(b => b.addEventListener('click', () => tab(b.dataset.rpTab)));
  document.querySelectorAll('[data-go]').forEach(b => b.addEventListener('click', () => tab(b.dataset.go)));

  // ── Ingredient picker (search box + list), used for "add to dish" / "add to ingredient"
  function picker(input, items, label, onPick) {
    const box = document.createElement('div');
    box.className = 'rp-pick'; box.hidden = true;
    input.after(box);
    input.addEventListener('input', () => {
      const q = input.value.trim().toLowerCase();
      const hits = q ? items.filter(x => label(x).toLowerCase().includes(q)).slice(0, 8) : [];
      box.innerHTML = hits.map((x, k) => `<div data-k="${k}">${esc(label(x))}</div>`).join('');
      box.hidden = !hits.length;
      box.onmousedown = e => { const el = e.target.closest('[data-k]'); if (!el) return; e.preventDefault(); const x = hits[+el.dataset.k]; input.value = label(x); box.hidden = true; onPick(x); };
    });
    input.addEventListener('blur', () => setTimeout(() => { box.hidden = true; }, 150));
  }

  // ── By dish
  let cur = +(SEL.dish || 0) || D.dishes[0]?.id;
  const changes = {}, removes = new Set(), adds = [];
  const dirty = () => Object.keys(changes).length + removes.size + adds.length;
  function dlist() {
    const q = $('[data-rp-dq]').value.trim().toLowerCase();
    $('[data-rp-dlist]').innerHTML = D.dishes.filter(d => !q || (d.n + ' ' + d.c).toLowerCase().includes(q)).map(d => {
      const est = (D.lines[d.id] || []).filter(l => l.src === 'estimate' && priced(l)).length;
      return `<button type="button" class="rp-li ${d.id === cur ? 'on' : ''}" data-d="${d.id}"><span><b>${esc(d.n)}</b><small>${esc(d.c)} · ${plates(d)} plates/30d${d.combo ? ' · combo' : est ? ` · ${est} estimate${est > 1 ? 's' : ''}` : ''}</small></span><span class="rp-fc ${fcc(fc(d))}">${fc(d).toFixed(0)}%</span></button>`;
    }).join('') || '<p class="sl-empty">No dishes match.</p>';
  }
  $('[data-rp-dlist]').addEventListener('click', e => {
    const b = e.target.closest('[data-d]'); if (!b) return;
    if (dirty() && !confirm('Discard unsaved changes to this recipe?')) return;
    Object.keys(changes).forEach(k => delete changes[k]); removes.clear(); adds.length = 0;
    cur = +b.dataset.d; dlist(); recipe();
    if (innerWidth < 860) $('[data-rp-recipe]').scrollIntoView({ behavior: 'smooth' });
  });
  $('[data-rp-dq]').addEventListener('input', dlist);

  function recipe() {
    const d = byId[cur], el = $('[data-rp-recipe]');
    if (!d) { el.innerHTML = '<p class="sl-empty">Pick a dish.</p>'; return; }
    const head = `<div class="sl-sechead rp-chead"><div><h3>${esc(d.n)}</h3><p>${esc(d.c)} · menu price ${rs(d.p)} · ${d.q} dine-in + ${d.qd} delivery plates in 30 days</p></div>${d.conf !== 'reliable' ? '<span class="rp-tag x">Provisional</span>' : ''}</div>`;
    let body = '';
    const L = (D.lines[d.id] || []).map(l => ({ ...l, g: changes[l.id] ?? l.g })).filter(l => !removes.has(l.id));
    if (d.combo) {
      body = `<table class="sl-table"><thead><tr><th>Part of the combo</th><th class="r">Portion</th><th class="r">Cost</th></tr></thead><tbody>${(D.combos[d.id] || []).map(c => {
        const cost = (c.id ? c.pf * recipeCost(c.id) : 0) + (c.fc || 0);
        return `<tr><td>${c.id ? `<a href="#" data-open="${c.id}"><b>${esc(c.n)}</b></a>` : '<b>Fixed item</b>'}${c.guess ? ' <span class="rp-tag x">guess</span>' : ''}</td><td class="r">${c.id ? '× ' + c.pf : rs(c.fc, 2) + ' fixed'}</td><td class="r"><b>${rs(cost, 2)}</b></td></tr>`; }).join('')}</tbody></table>
        <p class="rp-note">A combo is costed from its dishes' recipes, so change grams on those dishes. Packaging and spices are charged once per combo.</p>`;
    } else {
      const tmp = { ...d }; D.lines[d.id + ':tmp'] = L.concat(adds.map(a => ({ ...a, src: 'weighed' })));
      const rc = (D.lines[d.id + ':tmp']).reduce((s, l) => s + (lineCost(l) || 0), 0);
      const main = L.filter(l => l.cat !== 'Spices').sort((a, b) => (lineCost(b) ?? -1) - (lineCost(a) ?? -1));
      const sp = L.filter(l => l.cat === 'Spices');
      const est = L.filter(l => l.src === 'estimate' && changes[l.id] === undefined).length;
      body = `<table class="sl-table rp-lines"><thead><tr><th>Ingredient</th><th class="r">Grams</th><th class="sl-hide-sm">Grams are</th><th class="r sl-hide-sm">Price</th><th class="r">Cost</th><th></th></tr></thead><tbody>
        ${main.concat(sp).map(l => { const c = lineCost(l), edited = changes[l.id] !== undefined;
          return `<tr class="${l.cat === 'Spices' ? 'rp-spice' : ''}"><td><a href="#" data-ing="${l.iid}"><b>${esc(l.i)}</b></a>${l.cat === 'Spices' ? ' <small class="sl-muted">spice</small>' : ''}</td>
          <td class="r"><input class="rp-g ${edited ? 'ed' : ''}" data-line="${l.id}" value="${l.g ?? ''}" inputmode="decimal" aria-label="Grams of ${esc(l.i)}"> <small>${l.u}</small></td>
          <td class="sl-hide-sm">${l.conflict && !edited ? `<span class="rp-tag x">two weights</span> <button type="button" class="rp-mini" data-pick="${l.id}" data-v="${l.go}">${l.go}</button><button type="button" class="rp-mini" data-pick="${l.id}" data-v="${l.po}">${l.po}</button>`
            : edited || l.src === 'weighed' ? '<span class="rp-tag m">confirmed</span>' : '<span class="rp-tag e">estimate</span>'}</td>
          <td class="r sl-hide-sm sl-muted">${l.cat === 'Spices' ? 'in spice share' : l.kg != null ? rs(l.kg) + '/kg' : '<span class="rp-tag x">no price</span>'}</td>
          <td class="r">${c != null ? `<span class="rp-bar"><i style="width:${Math.min(100, rc ? c / rc * 100 : 0)}%"></i></span><b>${rs(c, 2)}</b>` : '<span class="sl-muted">—</span>'}</td>
          <td><button type="button" class="rp-x" data-rm="${l.id}" aria-label="Remove ${esc(l.i)}">×</button></td></tr>`; }).join('')}
        ${adds.map((a, k) => `<tr class="rp-new"><td><b>${esc(a.i)}</b> <span class="rp-tag m">new</span></td><td class="r">${a.g} <small>${a.u}</small></td><td class="sl-hide-sm"></td><td class="r sl-hide-sm sl-muted">${a.kg != null ? rs(a.kg) + '/kg' : ''}</td><td class="r">${lineCost(a) != null ? '<b>' + rs(lineCost(a), 2) + '</b>' : ''}</td><td><button type="button" class="rp-x" data-unadd="${k}">×</button></td></tr>`).join('')}
        </tbody></table>
        <div class="rp-add"><input data-rp-addname placeholder="Add an ingredient… (search)" autocomplete="off"><input data-rp-addg inputmode="decimal" placeholder="grams"><button type="button" class="sl-btn" data-rp-addbtn>Add</button></div>
        ${est ? `<div class="rp-estbar"><span>${est} line${est > 1 ? 's are' : ' is'} still an estimate. Type the real grams, or if they're right as they are:</span><button type="button" class="sl-btn" data-rp-confirm>Confirm ${est} as correct</button></div>` : ''}`;
      delete D.lines[d.id + ':tmp'];
      tmp.rc = rc;
      d._rc = rc;
    }
    const rc = d.combo ? dishCost(d) : d._rc, tot = rc + D.spice + d.pack, f = tot / d.p * 100;
    el.innerHTML = head + body + `
      <div class="rp-sum"><div><span>Recipe</span><b>${rs(rc, 2)}</b><small>ingredients above</small></div>
        <div><span>Spices + packaging</span><b>${rs(D.spice + d.pack, 2)}</b><small>${rs(D.spice, 2)} spices · ${rs(d.pack, 2)} packaging</small></div>
        <div><span>Food cost</span><b class="rp-fc ${fcc(f)}">${f.toFixed(1)}%</b><small>${rs(tot, 2)} of ${rs(d.p)}${dirty() && d.f != null ? ` · saved ${d.f}%` : ''}</small></div>
        <div><span>Per plate left</span><b>${rs(d.p - tot)}</b><small>× ${plates(d)} plates = ${rs((d.p - tot) * plates(d))}</small></div></div>
      ${dirty() ? `<div class="rp-savebar"><span><b>${dirty()} change${dirty() > 1 ? 's' : ''}</b>. Saving updates food cost now and what each sale takes off stock from the next upload.</span><button type="button" class="sl-btn" data-rp-undo>Undo</button><button type="button" class="sl-btn sl-btn-p" data-rp-save>Save recipe</button></div>` : ''}`;
    wire(d);
  }
  function wire(d) {
    const el = $('[data-rp-recipe]');
    el.querySelectorAll('[data-line]').forEach(inp => inp.addEventListener('change', () => {
      const v = parseFloat(inp.value), l = D.lines[d.id].find(x => x.id === +inp.dataset.line);
      if (!(v >= 0) || v > 5000) { inp.classList.add('bad'); return; }
      if (v === l.g && !l.conflict && l.src === 'weighed') delete changes[l.id]; else changes[l.id] = v;
      recipe();
    }));
    el.querySelectorAll('[data-pick]').forEach(b => b.addEventListener('click', () => { changes[+b.dataset.pick] = parseFloat(b.dataset.v); recipe(); }));
    el.querySelectorAll('[data-rm]').forEach(b => b.addEventListener('click', () => { removes.add(+b.dataset.rm); recipe(); }));
    el.querySelectorAll('[data-unadd]').forEach(b => b.addEventListener('click', () => { adds.splice(+b.dataset.unadd, 1); recipe(); }));
    el.querySelectorAll('[data-open]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); cur = +a.dataset.open; dlist(); recipe(); }));
    el.querySelectorAll('[data-ing]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); icur = +a.dataset.ing; ilist(); ingcard(); tab('ing'); }));
    const nameIn = el.querySelector('[data-rp-addname]');
    let picked = null;
    if (nameIn) {
      const have = new Set((D.lines[d.id] || []).map(l => l.iid).concat(adds.map(a => a.iid)));
      picker(nameIn, D.ings.filter(i => i.role === 'recipe' && !have.has(i.id)), i => i.name, i => { picked = i; el.querySelector('[data-rp-addg]').focus(); });
      el.querySelector('[data-rp-addbtn]').addEventListener('click', () => {
        const g = parseFloat(el.querySelector('[data-rp-addg]').value);
        if (!picked) { nameIn.focus(); return; }
        if (!(g > 0)) { el.querySelector('[data-rp-addg]').focus(); return; }
        adds.push({ iid: picked.id, i: picked.name, g, u: picked.u === 'l' || picked.u === 'ml' ? 'ml' : 'g', cat: picked.c, role: picked.role, kg: picked.kg });
        recipe();
      });
    }
    const undo = el.querySelector('[data-rp-undo]');
    if (undo) undo.addEventListener('click', () => { Object.keys(changes).forEach(k => delete changes[k]); removes.clear(); adds.length = 0; recipe(); });
    const save = el.querySelector('[data-rp-save]');
    if (save) save.addEventListener('click', async () => {
      save.disabled = true;
      try {
        await post(`/recipes/dish/${d.id}`, {
          changes: Object.entries(changes).map(([id, grams]) => ({ id: +id, grams })),
          adds: adds.map(a => ({ ingredient_id: a.iid, grams: a.g })), removes: [...removes],
        });
        Object.keys(changes).forEach(k => delete changes[k]); removes.clear(); adds.length = 0;
        reload({ dish: d.id });
      } catch (err) { alert(err.message); save.disabled = false; }
    });
    const conf = el.querySelector('[data-rp-confirm]');
    if (conf) conf.addEventListener('click', async () => {
      if (dirty() && !confirm('Save your grams changes first? Confirming reloads the recipe.')) return;
      conf.disabled = true;
      try { await post(`/recipes/dish/${d.id}/confirm`); Object.keys(changes).forEach(k => delete changes[k]); removes.clear(); adds.length = 0; reload({ dish: d.id }); } catch (err) { alert(err.message); conf.disabled = false; }
    });
  }

  // ── By ingredient
  let icur = +(SEL.ing || 0) || (D.ings.find(i => i.name === 'Paneer') || D.ings[0])?.id;
  function ilist() {
    const q = $('[data-rp-iq]').value.trim().toLowerCase();
    $('[data-rp-ilist]').innerHTML = D.ings.filter(i => !q || i.name.toLowerCase().includes(q)).map(i =>
      `<button type="button" class="rp-li ${i.id === icur ? 'on' : ''}" data-i="${i.id}"><span><b>${esc(i.name)}</b><small>${esc(i.c || '—')} · ${i.role !== 'recipe' ? 'not a recipe item' : i.nd + ' dish' + (i.nd === 1 ? '' : 'es')}</small></span><span class="sl-muted rp-price">${i.kg != null ? rs(i.kg) + '/kg' : ''}</span></button>`).join('');
  }
  $('[data-rp-ilist]').addEventListener('click', e => { const b = e.target.closest('[data-i]'); if (b) { icur = +b.dataset.i; ilist(); ingcard(); } });
  $('[data-rp-iq]').addEventListener('input', ilist);
  function ingcard() {
    const i = ingById[icur], el = $('[data-rp-ingcard]');
    if (!i) { el.innerHTML = '<p class="sl-empty">Pick an ingredient.</p>'; return; }
    // Dishes using it directly, plus combos through their parts (Combo 01 =
    // half a Jeera Rice), the way stock deduction counts them. A combo's own
    // legacy lines are skipped, as the cost engine does.
    const uses = [];
    D.dishes.forEach(d => {
      if (d.combo) {
        (D.combos[d.id] || []).forEach(c => (c.id ? D.lines[c.id] || [] : []).forEach(l => {
          if (l.iid === i.id) uses.push({ d, l: { ...l, g: l.g != null ? +(l.g * c.pf).toFixed(2) : null, via: `${c.pf} × ${byId[c.id]?.n || 'dish'}` } });
        }));
      } else (D.lines[d.id] || []).forEach(l => { if (l.iid === i.id) uses.push({ d, l }); });
    });
    uses.sort((a, b) => (b.l.g || 0) * plates(b.d) - (a.l.g || 0) * plates(a.d));
    const grams = uses.reduce((s, u) => s + (u.l.g || 0) * plates(u.d), 0);
    const div = i.u === 'kg' || i.u === 'l' ? 1000 : i.u === 'pcs' && i.pack_g ? i.pack_g : 1;
    const unit = i.u === 'l' ? 'L' : i.u;
    const use = grams / div, gap = i.bought ? (i.bought - use) / i.bought * 100 : null;
    const gapc = gap == null ? '' : Math.abs(gap) <= 15 ? 'ok' : Math.abs(gap) <= 35 ? 'mid' : 'hi';
    el.innerHTML = `
      <div class="sl-sechead rp-chead"><div><h3>${esc(i.name)}</h3><p>${esc(i.c || '—')} · ${i.kg != null ? rs(i.kg) + '/kg average paid' : 'no purchase price yet'} · in ${uses.length} dish${uses.length === 1 ? '' : 'es'}</p></div>
        ${i.role === 'recipe' ? `<button type="button" class="sl-btn" data-role="overhead">Not a recipe item</button>` : `<button type="button" class="sl-btn" data-role="recipe">Make it a recipe item</button>`}</div>
      ${i.role === 'recipe' ? `<div class="rp-use"><div><span>Recipes say</span><b>${use.toFixed(1)} ${unit}</b><small>grams × plates, ${D.since.slice(5)} to ${D.asof.slice(5)}, all channels</small></div>
        <div><span>Bought</span><b>${(i.bought || 0).toFixed(1)} ${unit}</b><small>kitchen purchases, same days</small></div>
        <div><span>Gap</span><b class="rp-fc ${gapc}">${gap == null ? '—' : (gap > 0 ? '+' : '') + gap.toFixed(0) + '%'}</b><small>${gap == null ? 'nothing bought in these days' : gap > 15 ? 'bought more than recipes use: real portions may be bigger' : gap < -15 ? 'recipes use more than was bought: grams may be too high' : 'recipes match what you buy'}</small></div></div>` : '<p class="rp-note">Not a recipe item: not costed per dish and not taken off stock by sales.</p>'}
      <table class="sl-table"><thead><tr><th>Dish</th><th class="r">Grams</th><th class="sl-hide-sm">Grams are</th><th class="r">Plates</th><th class="r">Used</th></tr></thead><tbody>
      ${uses.map(u => `<tr><td><a href="#" data-open="${u.d.id}"><b>${esc(u.d.n)}</b></a>${u.l.via ? `<small class="sl-muted"> via ${esc(u.l.via)}</small>` : ''}</td><td class="r">${u.l.g ?? '—'} <small>${u.l.u}</small></td>
        <td class="sl-hide-sm">${u.l.conflict ? '<span class="rp-tag x">two weights</span>' : u.l.src === 'weighed' ? '<span class="rp-tag m">confirmed</span>' : '<span class="rp-tag e">estimate</span>'}</td>
        <td class="r">${plates(u.d)}</td><td class="r"><b>${((u.l.g || 0) * plates(u.d) / div).toFixed(div === 1 ? 0 : 2)} ${unit}</b></td></tr>`).join('') || '<tr><td colspan="5" class="sl-muted">Not in any recipe yet.</td></tr>'}
      </tbody></table>
      ${i.role === 'recipe' ? `<div class="rp-add"><input data-rp-adddish placeholder="Add ${esc(i.name)} to a dish… (search)" autocomplete="off"><input data-rp-addg2 inputmode="decimal" placeholder="grams"><button type="button" class="sl-btn" data-rp-add2>Add</button></div>` : ''}`;
    el.querySelectorAll('[data-open]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); cur = +a.dataset.open; dlist(); recipe(); tab('dish'); }));
    el.querySelectorAll('[data-role]').forEach(b => b.addEventListener('click', async () => {
      const toRole = b.dataset.role;
      if (toRole !== 'recipe' && uses.length && !confirm(`${i.name} is in ${uses.length} dishes. As a non-recipe item it stops counting towards their food cost and stock. Continue?`)) return;
      b.disabled = true;
      try { await post(`/recipes/ingredient/${i.id}/role`, { role: toRole }); reload({ tab: 'ing', ing: i.id }); } catch (err) { alert(err.message); b.disabled = false; }
    }));
    const dn = el.querySelector('[data-rp-adddish]');
    if (dn) {
      let pd = null;
      const have = new Set(uses.map(u => u.d.id));
      picker(dn, D.dishes.filter(d => !d.combo && !have.has(d.id)), d => d.n, d => { pd = d; el.querySelector('[data-rp-addg2]').focus(); });
      el.querySelector('[data-rp-add2]').addEventListener('click', async e => {
        const g = parseFloat(el.querySelector('[data-rp-addg2]').value);
        if (!pd) { dn.focus(); return; }
        if (!(g > 0)) { el.querySelector('[data-rp-addg2]').focus(); return; }
        e.target.disabled = true;
        try { await post(`/recipes/dish/${pd.id}`, { adds: [{ ingredient_id: i.id, grams: g }] }); reload({ tab: 'ing', ing: i.id }); } catch (err) { alert(err.message); e.target.disabled = false; }
      });
    }
  }

  // ── Needs attention
  function att() {
    const parts = [];
    if (conflicts.length) parts.push(`<div class="rp-att"><span class="rp-ic bad">${conflicts.length}</span><div><b>${conflicts.length} line${conflicts.length > 1 ? 's have' : ' has'} two different weights saved</b>
      <p>Pick the right one. It's saved straight away and used for food cost and stock.</p>
      ${conflicts.map(c => `<div class="rp-pickrow"><span>${esc(c.d.n)} · <b>${esc(c.l.i)}</b></span><button type="button" class="sl-btn" data-fix="${c.d.id}:${c.l.id}:${c.l.go}">${c.l.go} ${c.l.u}</button><button type="button" class="sl-btn" data-fix="${c.d.id}:${c.l.id}:${c.l.po}">${c.l.po} ${c.l.u}</button></div>`).join('')}</div></div>`);
    if (estDishes.length) {
      const top = estDishes.slice().sort((a, b) => plates(b) - plates(a)).slice(0, 12);
      parts.push(`<div class="rp-att"><span class="rp-ic warn">g</span><div><b>${estDishes.length} dishes still use estimated grams</b>
        <p>Their grams were copied from the old light / medium / heavy portions. Weigh the busiest dishes first; they decide most of your food cost and stock.</p>
        <div class="rp-chips">${top.map(d => `<a href="#" class="rp-chip" data-open="${d.id}">${esc(d.n)} · ${plates(d)} plates · ${(D.lines[d.id] || []).filter(l => l.src === 'estimate' && priced(l)).length} est.</a>`).join('')}</div></div></div>`);
    }
    if (unpriced.length) parts.push(`<div class="rp-att"><span class="rp-ic warn">₹</span><div><b>${unpriced.map(i => esc(i.name)).join(', ')} ${unpriced.length > 1 ? 'have' : 'has'} no price</b><p>Used in a recipe but never bought as a kitchen purchase, so those lines cost ₹0. Log a purchase on <a href="/purchases">Purchases</a>.</p></div></div>`);
    if (noRecipe.length) parts.push(`<div class="rp-att"><span class="rp-ic warn">${noRecipe.length}</span><div><b>${noRecipe.length} kitchen items are in no recipe</b>
      <p>Bought as ingredients but no dish uses them, so they never count towards food cost or come off stock. Open one to add it to its dishes, or mark it as not a recipe item.</p>
      <div class="rp-chips">${noRecipe.map(i => `<a href="#" class="rp-chip" data-ingopen="${i.id}">${esc(i.name)}</a>`).join('')}</div></div></div>`);
    $('[data-rp-att]').innerHTML = parts.join('') || '<p class="sl-empty">Nothing needs attention.</p>';
    $('[data-rp-att]').querySelectorAll('[data-open]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); cur = +a.dataset.open; dlist(); recipe(); tab('dish'); }));
    $('[data-rp-att]').querySelectorAll('[data-ingopen]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); icur = +a.dataset.ingopen; ilist(); ingcard(); tab('ing'); }));
    $('[data-rp-att]').querySelectorAll('[data-fix]').forEach(b => b.addEventListener('click', async () => {
      const [dish, line, grams] = b.dataset.fix.split(':');
      b.disabled = true;
      try { await post(`/recipes/dish/${dish}`, { changes: [{ id: +line, grams: +grams }] }); reload({ tab: 'att' }); } catch (err) { alert(err.message); b.disabled = false; }
    }));
  }

  dlist(); recipe(); ilist(); ingcard(); att();
  if (SEL.tab) tab(SEL.tab);
  window.addEventListener('beforeunload', e => { if (dirty()) { e.preventDefault(); e.returnValue = ''; } });
})();
