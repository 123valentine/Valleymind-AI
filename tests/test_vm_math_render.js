// Math rendering regressions: $...$, $$...$$ and \[...\] must never leave raw
// \frac / \boxed backslashes in an assistant bubble when KaTeX is unavailable.
const fs = require('fs');
const vm = require('vm');

let ok = 0, fail = 0;
function assert(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0, 200) : '')); fail++; }
}

function makeWalker() { return { nextNode: function () { return null; } }; }
function StubDOMParser() {}
StubDOMParser.prototype.parseFromString = function (html) {
  return { body: { innerHTML: html, createTreeWalker: makeWalker }, createTreeWalker: makeWalker };
};
const stubMarked = function (md) { return '<p>' + String(md || '') + '</p>'; };
stubMarked.parse = stubMarked;

const ctx = {
  window: {},
  document: {
    addEventListener: function () {},
    querySelectorAll: function () { return []; },
  },
  DOMParser: StubDOMParser,
  marked: stubMarked,
  console: console,
  Promise: Promise,
  Object: Object,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  Date: Date,
  NodeFilter: { SHOW_ELEMENT: 1, SHOW_COMMENT: 128 },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('static/settings.js', 'utf8'), ctx, { filename: 'settings.js' });
vm.runInContext(fs.readFileSync('static/vm_richrender.js', 'utf8'), ctx, { filename: 'vm_richrender.js' });

assert('VMFormatRichBubble is installed', typeof ctx.window.VMFormatRichBubble === 'function');
assert('VMRenderMath is available from settings.js', typeof ctx.window.VMRenderMath === 'function');

const bubble = {
  innerHTML: '<p>Value $\\frac{a}{b}$ and $\\boxed{x^2}$ plus \\[\\sqrt{9}\\].</p>',
  dataset: {},
  classList: { add: function () {}, contains: function () { return false; } },
  querySelectorAll: function () { return []; },
  querySelector: function () { return null; },
};

ctx.window.VMFormatRichBubble(bubble);
const once = bubble.innerHTML;
assert('inline math has no raw \\frac', once.indexOf('\\frac') === -1, once);
assert('boxed math has no raw \\boxed', once.indexOf('\\boxed') === -1, once);
assert('fraction renders as readable Unicode', once.indexOf('(a)/(b)') !== -1, once);
assert('boxed expression renders with Unicode brackets', once.indexOf('⟦x^2⟧') !== -1, once);
assert('bracket-delimited square root renders', once.indexOf('√9') !== -1, once);
assert('math fallback nodes are marked', once.indexOf('vm-math-fallback') !== -1, once);

ctx.window.VMFormatRichBubble(bubble);
assert('math formatting is idempotent', bubble.innerHTML === once);

console.log('\nVM_MATH_RENDER_TESTS: ' + ok + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
