// Exercise the actual page script with minimal DOM/network doubles. No orders
// or OCR requests leave this process.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../app/web/templates/rush.html'), 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) nodes.set(id, {
    value: '', checked: false, hidden: false, disabled: false, files: [],
    textContent: '', innerHTML: '', dataset: {},
    classList: {toggle() {}, remove() {}, add() {}},
    remove() {}, removeAttribute() {}, focus() {}, scrollIntoView() {}, click() {},
  });
  return nodes.get(id);
}
node('#rush-data').textContent = JSON.stringify({types: ['Dine-in', 'Parcel'], menu: [{id: 1, name: 'Veg Fried Rice'}]});
const row = {querySelector: s => s === 'select' ? {value: '1'} : {value: '1'}};
const board = {printed: '9:24', slips: [], orders: [], pack: []};
const document = {
  getElementById: id => node('#' + id), querySelector: node,
  querySelectorAll: s => s === '#scanrows .scanrow' ? [row] : [],
};
const sandbox = {document, navigator: {userAgent: 'URSRushApp'},
  window: {scrollTo() {}}, localStorage: {getItem() {}, setItem() {}},
  setTimeout() {}, clearTimeout() {}, setInterval() {}, AbortController,
  FileReader: class {readAsDataURL() {this.result = 'data:image/jpeg;base64,AA'; this.onload();}},
  FormData: class {append() {}}, confirm: () => true,
  fetch: async url => ({ok: true, json: async () => url === '/rush/scan' ? {
    kot_no: '', bill_no: '15', order_reference: 'Bill 15', order_type: 'Dine-in',
    items: [{name: 'Veg Fried Rice', menu_item_id: 1, qty: 1}], warnings: [], raw_text: '',
  } : board}),
};
vm.runInNewContext(source, sandbox);
async function scan() {
  await node('#photo').onchange({target: {files: [{size: 100}], value: 'photo.jpg'}});
}
(async () => {
  node('#kot').value = 'old draft';
  await scan();
  assert.equal(node('#kot').value, 'Bill 15', 'number fills immediately, before accepting review');
  assert.equal(node('#send').disabled, true, 'review still required');
  node('#cancelscan').onclick();
  assert.equal(node('#kot').value, 'old draft', 'discard restores original reference');
  await scan();
  node('#kot').value = 'Bill 16';
  node('#scanchecked').checked = true;
  node('#usescan').onclick();
  assert.equal(node('#kot').value, 'Bill 16', 'manual correction survives accepting the scan');
  assert.equal(node('#send').disabled, false);
  console.log('Rush UI: immediate number capture, discard, correction and review checks passed');
})().catch(e => {console.error(e); process.exitCode = 1;});
