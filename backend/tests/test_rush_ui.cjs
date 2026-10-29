// Exercise the actual page script with minimal DOM/network doubles. No orders
// or OCR requests leave this process.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../app/web/templates/rush.html'), 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const nativeMode = process.argv.includes('--native');
let nativeCalls = 0;
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
let failNextScan = false, uploads = 0;
const sandbox = {document, navigator: {userAgent: 'URSRushApp'},
  window: {scrollTo() {}}, localStorage: {getItem() {}, setItem() {}},
  setTimeout() {}, clearTimeout() {}, setInterval() {}, AbortController,
  FileReader: class {readAsDataURL() {this.result = 'data:image/jpeg;base64,AA'; this.onload();}},
  FormData: class {append() {}}, confirm: () => true,
  fetch: async (url, options = {}) => {
    if (url === '/rush/scan' || url === '/rush/scan-text') {
      assert.equal(url, nativeMode ? '/rush/scan-text' : '/rush/scan');
      if (nativeMode) {
        assert.equal(typeof options.body, 'string');
        assert.equal(typeof JSON.parse(options.body).text, 'string');
        assert.ok(!options.body.includes('data:image/'), 'native path never uploads photo data');
      }
      uploads++;
      if (failNextScan) { failNextScan = false; throw new Error('Photo reader unavailable'); }
    }
    return {ok: true, json: async () => (url === '/rush/scan' || url === '/rush/scan-text') ? {
    kot_no: '', bill_no: '15', order_reference: 'Bill 15', order_type: 'Dine-in',
    items: [{name: 'Veg Fried Rice', menu_item_id: 1, qty: 1}], warnings: [], raw_text: '',
  } : board};
  },
};
if (nativeMode) sandbox.window.URSOCR = {recognize(data, id) {
  nativeCalls++;
  assert.ok(data.startsWith('data:image/'));
  sandbox.window.__rushOCRResult(id, {text: 'Bill No.: 15\nCashier: biller\nVeg Fried Rice\n(Q) 1 x 129 = 129'});
}};
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
  failNextScan = true;
  await scan();
  assert.equal(node('#retryscan').hidden, false);
  const beforeRetry = uploads;
  const callsBeforeRetry = nativeCalls;
  await node('#retryscan').onclick();
  assert.equal(uploads, beforeRetry + 1, 'retry reuses the photo without choosing another file');
  assert.equal(node('#kot').value, 'Bill 15');
  assert.equal(node('#retryscan').hidden, true);
  assert.equal(node('#send').disabled, true, 'retry still requires review');
  if (nativeMode) {
    assert.ok(nativeCalls > 0);
    assert.equal(nativeCalls, callsBeforeRetry, 'network retry reuses local text without another OCR run');
  }
  console.log(`Rush UI (${nativeMode ? 'on-device' : 'hosted'}): reference, discard, correction, review and retry checks passed`);
})().catch(e => {console.error(e); process.exitCode = 1;});
