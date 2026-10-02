const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
let handle, ids = ['first', 'second'], hits = [], links = [];
const feedback = {textContent: ''};
const card = {disabled: false, dataset: {artifactId: 'second', fileAction: 'download'},
    matches: s => s === '.model-file-card', querySelector: () => feedback};
const native = {querySelector: () => ({textContent: JSON.stringify(ids)}), querySelectorAll: () => links};
const input = {value: '', dispatchEvent: () => hits.push('input:' + input.value)};
const trigger = {click: () => hits.push('retry:' + input.value)};
const document = {addEventListener: (name, fn) => {handle = fn;},
    querySelector: s => s === '#model-output-native-files' ? native : s === '#model-output-retry' ? trigger : input};
vm.runInNewContext(fs.readFileSync('web_assets/javascript/artifact-cards.js', 'utf8'),
    {document, Event: class {}, requestAnimationFrame: fn => fn()});
const click = () => handle({composedPath: () => [card]});
// Duplicate display names never decide which actual native download is used.
links = [{click: () => hits.push('file-first')}, {click: () => hits.push('file-second')}];
click(); assert.deepStrictEqual(hits, ['file-second']);
// New native render reorders links. The same artifact ID follows its own link.
ids = ['second', 'first']; links.reverse(); click();
assert.deepStrictEqual(hits, ['file-second', 'file-second']);
ids = ['unrelated']; click(); assert.strictEqual(hits.length, 2);
assert(feedback.textContent.includes('正在准备'));
// Retry uses the real hidden input and button, with duplicate-click protection.
card.dataset.fileAction = 'retry'; click(); card.disabled = true; click();
assert.deepStrictEqual(hits.slice(2), ['input:second', 'retry:second']);
assert(card.disabled);
console.log('Artifact cards: stable IDs, native downloads, stale render guard, retry and rerender delegation passed');
