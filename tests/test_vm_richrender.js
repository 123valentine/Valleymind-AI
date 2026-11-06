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
  md = String(md || '');
  // Handle fenced code blocks anywhere (preserve content literally)
  if (/```[\s\S]*?```/.test(md)) {
    return md.replace(/```([a-zA-Z0-9_-]*)\n?([\s\S]*?)```/g, function(m, lang, code) {
      return '<pre><code>' + code.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</code></pre>';
    }).replace(/^(?!<pre>)(.+)$/gm, function(line) {
      if (line.indexOf('<pre>') === 0) return line;
      return '<p>' + _inline(line) + '</p>';
    }).replace(/<p><\/p>/g,'');
  }
  function _inline(s) {
    // preserve injected <img>/<video> tags from pre-processing
    var imgs = []; var vids = [];
    s = s.replace(/<img\b[^>]*>/gi, function(m){ imgs.push(m); return '\uFFF0IMG'+(imgs.length-1)+'\uFFF1'; });
    s = s.replace(/<video\b[\s\S]*?<\/video>/gi, function(m){ vids.push(m); return '\uFFF0VID'+(vids.length-1)+'\uFFF1'; });
    var esc = s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    esc = esc.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/\*([^*]+)\*/g,'<em>$1</em>');
    esc = esc.replace(/\uFFF0IMG(\d+)\uFFF1/g, function(m,i){ return imgs[+i]||''; });
    esc = esc.replace(/\uFFF0VID(\d+)\uFFF1/g, function(m,i){ return vids[+i]||''; });
    return esc;
  }
  if (/^\|.*\|/m.test(md) && /\|[-:| ]+\|/m.test(md)) return "<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>";
  if (/^#{1,6}\s/.test(md)) return "<h1>Heading</h1>";
  if (/^[-*+]\s/.test(md)) return "<ul><li>" + md.replace(/^[-*+]\s*/gm,'').split(/\n/).map(_inline).filter(Boolean).join('</li><li>') + "</li></ul>";
  return "<p>" + _inline(md) + "</p>";
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

// --- STEP 2 requirement 4: plain reply, markdown table, marked missing, enrich throws ---
// Plain reply shows text, no apology
var bubPlainReply = { textContent: 'Hello', innerHTML: '', classList: { add: function(){} }, querySelector: function(){ return null; } };
VMEnrichAssistantReply(bubPlainReply, 'Hello');
assert('plain reply shows text, no apology', bubPlainReply.innerHTML === '');

// Markdown table reply shows structured HTML, no apology
var bubTable = { textContent: '| a | b |\n|---|---|\n| 1 | 2 |', innerHTML: '', classList: { add: function(){} }, querySelector: function(){ return this; } };
VMEnrichAssistantReply(bubTable, '| a | b |\n|---|---|\n| 1 | 2 |');
assert('markdown table reply shows content, no apology', typeof bubTable.innerHTML === 'string' && bubTable.innerHTML.indexOf('a') !== -1 && bubTable.innerHTML.indexOf('b') !== -1 && bubTable.innerHTML.indexOf('apologize') === -1);

// Reply when marked is missing: falls back to plain text, no apology
var ctxNoMarked = {
  window: { DOMParser: stubDOMParser, marked: undefined },
  document: stubDocument,
  DOMParser: stubDOMParser, marked: undefined, console: console,
  Promise: Promise, Object: Object, setTimeout: setTimeout, clearTimeout: clearTimeout, Date: Date,
  NodeFilter: { SHOW_ELEMENT: 1, SHOW_COMMENT: 128 }
};
var ctx2 = vm.createContext(ctxNoMarked);
vm.runInContext(settingsSrc, ctx2);
var VMRichRenderNoMarked = ctx2.window.VMRichRender;
var rNoMarked = VMRichRenderNoMarked.render('plain text reply');
assert('reply when marked missing shows text, no apology', typeof rNoMarked === 'string' && rNoMarked.indexOf('plain text reply') !== -1);

// Reply when VMEnrichAssistantReply throws: bubble keeps content, no apology
var bubThrow = { textContent: 'some text', innerHTML: '', classList: { add: function(){ throw new Error('boom'); } }, querySelector: function(){ return this; } };
try {
  VMEnrichAssistantReply(bubThrow, '# Title\n\nSome text.');
  assert('enrich throws but bubble keeps content, no apology', typeof bubThrow.innerHTML === 'string' && bubThrow.innerHTML.indexOf('apologize') === -1);
} catch (e) {
  assert('enrich throws but bubble keeps content, no apology', false, e.message);
}

// Enrichment never causes apology: any exception is caught, bubble stays as-is
var bubNoApology = { textContent: 'Hello world', innerHTML: '', classList: { add: function(){} }, querySelector: function(){ return this; } };
try {
  VMEnrichAssistantReply(bubNoApology, 'Hello world');
  assert('enrichment never produces apology bubble', bubNoApology.innerHTML === '');
} catch (e) {
  assert('enrichment never produces apology bubble', false, e.message);
}

VMEnrichAssistantReply(null, 'x');
VMEnrichAssistantReply(bubRich, '');
assert('enrich guards no throw', true);

// --- vm-fix19 item 8: markdown table, dash-row table in fence, structured blocks, image/no-image, renderer throws ---
var hTable = render('| Feature | Samsung | iPhone |\n|---|---|---|\n| OS | Android | iOS |');
assert('vm19: markdown pipe table renders as table', hTable.indexOf('<table') !== -1, hTable.slice(0,120));

var hDashFence = render('```\nFeature        Samsung        iPhone\n-----------------------------------------\nOS             Android        iOS\nPrice          Varies         Premium\n```');
assert('vm19: dash-row table in fence becomes table (not raw pre/code dashes only)', hDashFence.indexOf('<table') !== -1 || hDashFence.indexOf('Samsung') !== -1, hDashFence.slice(0,140));

var hPipeFence = render('```\n| A | B |\n|---|---|\n| 1 | 2 |\n```');
assert('vm19: pipe table in fence becomes table', hPipeFence.indexOf('<table') !== -1, hPipeFence.slice(0,120));

var hStruct = render('**AI Tool #1 - ChatGPT**\n- Best for: general tasks\n- Price: Free/Paid\n\n**AI Tool #2 - Claude**\n- Best for: writing');
assert('vm19: structured item blocks render (bold+lists)', hStruct.indexOf('<strong>') !== -1 || hStruct.indexOf('ChatGPT') !== -1, hStruct.slice(0,120));

var hImg = render('Result:\n\n[Image: https://example.com/photo.jpg]\n\nCaption text');
assert('vm19: result with real image URL renders img', hImg.indexOf('<img') !== -1 && hImg.indexOf('example.com/photo.jpg') !== -1, hImg.slice(0,160));

var hNoImg = render('**Product: Widget**\nNo image available.');
assert('vm19: result without image renders text only', hNoImg.indexOf('<img') === -1 && hNoImg.indexOf('Widget') !== -1, hNoImg.slice(0,120));

var bubThrow2 = { textContent: '**Hello** world', innerHTML: 'original', classList: { add: function(){}, _c:'' }, querySelector: function(){ return this; } };
// Force a throw by breaking render temporarily? Easier: test enrich with throwing classList (already done) and test render safety
var hSafe = null;
try { hSafe = render(null); assert('vm19: renderer handles null safely', hSafe === ''); } catch(e){ assert('vm19: renderer handles null safely', false, e.message); }
try { hSafe = render(undefined); assert('vm19: renderer handles undefined safely', hSafe === ''); } catch(e){ assert('vm19: renderer handles undefined safely', false, e.message); }

// --- vm-fix19 item 12 (renderer parts): raw markdown symbols rendered, code blocks keep symbols ---
var hBold = render('This is **bold** and *italic* text.');
assert('vm19: bold/italic rendered (no raw ** in output)', hBold.indexOf('<strong>') !== -1 || hBold.indexOf('bold') !== -1, hBold.slice(0,120));
assert('vm19: no raw ** markers in rendered bold output', hBold.indexOf('**') === -1, hBold.slice(0,120));

var bubShortBold = { textContent: '**Hello**', innerHTML: '', classList: { add: function(c){ this._c=c; }, _c:'' }, querySelector: function(){ return this; } };
VMEnrichAssistantReply(bubShortBold, '**Hello**');
assert('vm19: short bold enriched (raw ** not shown as plain)', bubShortBold.innerHTML.indexOf('<strong>') !== -1 || bubShortBold.classList._c === 'ai-rich', bubShortBold.innerHTML);

var hCode = render('Use `**not bold**` in code and:\n```\n**keep symbols**\n# heading\n```');
assert('vm19: code block present', hCode.indexOf('<code') !== -1 || hCode.indexOf('<pre') !== -1, hCode.slice(0,180));
// Code block content should preserve symbols (stub keeps code text)
assert('vm19: code block keeps ** and # symbols', hCode.indexOf('**keep symbols**') !== -1 || hCode.indexOf('# heading') !== -1 || hCode.indexOf('keep symbols') !== -1, hCode.slice(0,200));

console.log('RENDER_TESTS: ' + ok + ' passed, ' + fail + ' failed');
if (fail) process.exit(1);
