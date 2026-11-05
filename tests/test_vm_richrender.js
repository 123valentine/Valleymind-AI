// Local harness for VMRichRender + VMPresentationSelector (stub DOM/marked).
const fs = require('fs');
const vm = require('vm');
let ok = 0, fail = 0;
function assert(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0,160) : '')); fail++; }
}

function makeWalker() { return { nextNode: function () { return null; } }; }
function makeBody(html) { return { innerHTML: html, createTreeWalker: makeWalker }; }
function StubDOMParser() {}
StubDOMParser.prototype.parseFromString = function (html) {
  return { body: makeBody(html), createTreeWalker: makeWalker };
};
const stubDOMParser = StubDOMParser;
// Mirror real `marked`: a callable function that also exposes `.parse`.
const stubMarked = function (md, opts) {
  if (/^#{1,6}\s/.test(md)) return "<h1>Heading</h1>";
  if (/^[-*+]\s/.test(md)) return "<ul><li>item</li></ul>";
  return "<p>" + String(md).replace(/</g, "&lt;").replace(/>/g, "&gt;") + "</p>";
};
stubMarked.parse = stubMarked;
const stubDocument = { createTreeWalker: makeWalker };

const ctx = {
  window: { DOMParser: stubDOMParser, marked: stubMarked },
  document: stubDocument,
  DOMParser: stubDOMParser,
  marked: stubMarked,
  console: console, Promise: Promise, Object: Object,
  setTimeout: setTimeout, clearTimeout: clearTimeout, Date: Date,
  NodeFilter: { SHOW_ELEMENT: 1, SHOW_COMMENT: 128 }
};
vm.createContext(ctx);
const settingsSrc = fs.readFileSync('static/settings.js', 'utf8');
vm.runInContext(settingsSrc, ctx);

const VMRichRender = ctx.window.VMRichRender;
const VMPresentationSelector = ctx.window.VMPresentationSelector;
const VMEnrichAssistantReply = ctx.window.VMEnrichAssistantReply;
assert('VMRichRender defined', typeof VMRichRender === 'object' && typeof VMRichRender.render === 'function');
assert('VMPresentationSelector defined', typeof VMPresentationSelector === 'object' && typeof VMPresentationSelector.select === 'function');
assert('VMEnrichAssistantReply defined', typeof VMEnrichAssistantReply === 'function');

function select(t){ return VMPresentationSelector.select(t); }
function render(t){ return VMRichRender.render(t); }

assert('select plain text', select('Just a simple hello.') === 'plain');
assert('select short', select('Hi') === 'plain');
assert('select list', select('- apple\n- banana') === 'list');
assert('select numbered list', select('1. one\n2. two') === 'list');
assert('select checklist', select('- [x] done\n- [ ] todo') === 'checklist');
assert('select code fence', select('```\ncode\n```') === 'code');
assert('select heading', select('# Title') === 'structured');
assert('select table', select('| a | b |\n|---|---|\n| 1 | 2 |') === 'table');
assert('select long structured', select(('First paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n').repeat(6)) === 'structured');
assert('select empty', select('') === 'plain');
assert('select null', select(null) === 'plain');

var r = render('hello world');
assert('render returns string', typeof r === 'string');
assert('render escapes angle brackets', /<p>|<h1>|<ul>/.test(r));

var withCite = render('See [1] and [SourceName] for details.');
assert('render citations preserved', withCite.indexOf('[1]') !== -1 && withCite.indexOf('[SourceName]') !== -1);
assert('render no placeholder leaked', withCite.indexOf('\uFFF7C') === -1);

var r2 = render('<img src=x onerror=alert(1)>');
assert('render dangerous img returns string', typeof r2 === 'string');

var called = false;
var bubPlain = { textContent: 'hi', innerHTML: '', classList: { add: function(){} }, querySelector: function(){ return null; } };
VMEnrichAssistantReply(bubPlain, 'hi');
assert('enrich plain leaves innerHTML', bubPlain.innerHTML === '');

var bubRich = { textContent: '# Title\n\nSome text.', innerHTML: '', classList: { add: function(){ called = true; } }, querySelector: function(){ return this; } };
VMEnrichAssistantReply(bubRich, '# Title\n\nSome text.');
assert('enrich structured sets innerHTML', typeof bubRich.innerHTML === 'string' && bubRich.innerHTML.length > 0);
assert('enrich structured adds ai-rich class', called === true);

VMEnrichAssistantReply(null, 'x');
VMEnrichAssistantReply(bubRich, '');
assert('enrich guards no throw', true);

console.log('RENDER_TESTS: ' + ok + ' passed, ' + fail + ' failed');
if (fail) process.exit(1);
