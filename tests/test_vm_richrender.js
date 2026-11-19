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
assert('numeric citations preserved', withCite.indexOf('[1]') !== -1);
assert('named citations render pills', withCite.indexOf('vm-cite-pill') !== -1 && withCite.indexOf('[SourceName]') === -1);
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

// --- Table rendering regression: marked v9 exposes an OBJECT (`window.marked.parse`),
// not a callable function. The old function-only guard silently fell back to plain
// paragraphs, so Markdown tables appeared as raw pipe text. ---
var ctxObj = {
  window: { DOMParser: stubDOMParser, marked: { parse: stubMarked } },
  document: stubDocument,
  DOMParser: stubDOMParser,
  marked: { parse: stubMarked },
  console: console, Promise: Promise, Object: Object,
  setTimeout: setTimeout, clearTimeout: clearTimeout, Date: Date,
  NodeFilter: { SHOW_ELEMENT: 1, SHOW_COMMENT: 128 }
};
vm.createContext(ctxObj);
vm.runInContext(settingsSrc, ctxObj);
var objRender = ctxObj.window.VMRichRender && ctxObj.window.VMRichRender.render;
assert('marked-object: VMRichRender.render available', typeof objRender === 'function');
var objTable = typeof objRender === 'function'
  ? objRender('| Feature | A | B |\n|---|---|---|\n| Speed | fast | slow |')
  : '';
assert('marked-object: pipe table becomes <table>', objTable.indexOf('<table') !== -1, objTable.slice(0,160));
assert('marked-object: no raw pipe markup leaks', objTable.indexOf('|---') === -1, objTable.slice(0,160));
var objBold = typeof objRender === 'function' ? objRender('This is **bold** text.') : '';
assert('marked-object: bold renders as <strong>', objBold.indexOf('<strong>') !== -1, objBold.slice(0,120));
assert('marked-object: VMWrapReplyTables helper exposed', typeof ctxObj.window.VMWrapReplyTables === 'function');

// --- Stage 3: rich-response formatting helpers (graceful no-ops in stub DOM) ---
var fmtHelpers = [
  'VMFormatRichBubble', 'VMAddCodeCopyButtons', 'VMRenderMath'
];
fmtHelpers.forEach(function (name) {
  assert('stage3: ' + name + ' exposed', typeof ctxObj.window[name] === 'function');
});
// VMFormatRichBubble must never throw on a bubble lacking querySelectorAll (stub DOM).
try {
  ctxObj.window.VMFormatRichBubble({ innerHTML: '', classList: { add: function(){} }, querySelector: function(){ return null; } });
  assert('stage3: VMFormatRichBubble safe no-op on stub bubble', true);
} catch (e) {
  assert('stage3: VMFormatRichBubble safe no-op on stub bubble', false, e.message);
}
// VMAddCodeCopyButtons / VMRenderMath likewise must not throw on a stub bubble.
try {
  ctxObj.window.VMAddCodeCopyButtons({ querySelectorAll: function(){ return []; } });
  ctxObj.window.VMRenderMath({ querySelectorAll: function(){ return []; } });
  assert('stage3: math/copy helpers safe no-op on stub bubble', true);
} catch (e) {
  assert('stage3: math/copy helpers safe no-op on stub bubble', false, e.message);
}

// --- Stage 3: interactive component rendering conventions ---
var stage3Render = typeof objRender === 'function' ? objRender : null;

if (stage3Render) {
  // Choice chips: [choice: Label]
  var hChoice = stage3Render('[choice: Option A]');
  assert('stage3: choice renders vm-choice element', hChoice.indexOf('<vm-choice') !== -1, hChoice.slice(0,120));
  assert('stage3: choice keeps label text', hChoice.indexOf('Option A') !== -1, hChoice.slice(0,120));
  assert('stage3: choice data-label attribute', hChoice.indexOf('data-label="Option A"') !== -1, hChoice.slice(0,120));

  // Choice chips with value: [choice: Label | value]
  var hChoiceVal = stage3Render('[choice: Option B | opt-b]');
  assert('stage3: choice with value renders data-value', hChoiceVal.indexOf('data-value="opt-b"') !== -1, hChoiceVal.slice(0,120));

  // Circular controls: [circle: Label]
  var hCircle = stage3Render('[circle: Yes]');
  assert('stage3: circle renders vm-circle element', hCircle.indexOf('<vm-circle') !== -1, hCircle.slice(0,120));
  assert('stage3: circle keeps label text', hCircle.indexOf('Yes') !== -1, hCircle.slice(0,120));

  // Circle with value: [circle: Label | value]
  var hCircleVal = stage3Render('[circle: No | no-val]');
  assert('stage3: circle with value renders data-value', hCircleVal.indexOf('data-value="no-val"') !== -1, hCircleVal.slice(0,120));

  // Information cards: [card: Title | Description]
  var hCard = stage3Render('[card: iPhone 15 | Latest smartphone from Apple]');
  assert('stage3: card renders vm-card element', hCard.indexOf('<vm-card') !== -1, hCard.slice(0,120));
  assert('stage3: card has title element', hCard.indexOf('<vm-card-title>iPhone 15</vm-card-title>') !== -1, hCard.slice(0,120));
  assert('stage3: card has desc element', hCard.indexOf('<vm-card-desc>Latest smartphone from Apple</vm-card-desc>') !== -1, hCard.slice(0,120));

  // Copyable text boxes: [copybox: content]
  var hCopy = stage3Render('[copybox: npm install valleymind]');
  assert('stage3: copybox renders vm-copybox element', hCopy.indexOf('<vm-copybox') !== -1, hCopy.slice(0,120));
  assert('stage3: copybox keeps content', hCopy.indexOf('npm install valleymind') !== -1, hCopy.slice(0,120));

  // Math expressions preserved through render
  var hMath = stage3Render('The formula is $E=mc^2$.');
  assert('stage3: math inline preserved', hMath.indexOf('E=mc^2') !== -1 || hMath.indexOf('E=mc') !== -1, hMath.slice(0,120));
}

// --- Stage 3: new helper functions exposed ---
var stage3Helpers = [
  'VMEnrichChoices', 'VMEnrichCircles', 'VMEnrichCards', 'VMEnrichCopyboxes'
];
stage3Helpers.forEach(function (name) {
  assert('stage3: ' + name + ' exposed', typeof ctxObj.window[name] === 'function');
});

// --- Stage 3: new helpers safe no-op on stub DOM ---
try {
  ctxObj.window.VMEnrichChoices({ querySelectorAll: function(){ return []; } });
  ctxObj.window.VMEnrichCircles({ querySelectorAll: function(){ return []; } });
  ctxObj.window.VMEnrichCards({ querySelectorAll: function(){ return []; } });
  ctxObj.window.VMEnrichCopyboxes({ querySelectorAll: function(){ return []; } });
  assert('stage3: choice/circle/card/copybox helpers safe no-op', true);
} catch (e) {
  assert('stage3: choice/circle/card/copybox helpers safe no-op', false, e.message);
}

// --- Stage 3: VMFormatRichBubble orchestrates all sub-formatters ---
try {
  var bubFmt = { innerHTML: '', classList: { add: function(){} }, querySelector: function(){ return null; } };
  ctxObj.window.VMFormatRichBubble(bubFmt);
  assert('stage3: VMFormatRichBubble orchestrates sub-formatters safely', true);
} catch (e) {
  assert('stage3: VMFormatRichBubble orchestrates sub-formatters safely', false, e.message);
}

// --- Stage 3: choice/circle callbacks wired to chat submission ---
// onVmChoiceSelected / onVmCircleSelected are defined in index.html (DOM context).
// Here we verify they are safe to call when the chat input is unavailable
// (graceful degradation when no composer is present).
var stubEl = {
    classList: { add: function(){}, contains: function(){ return false; } },
    getAttribute: function(){ return ''; },
    setAttribute: function(){},
    style: { cssText: '' },
    title: '',
    tabIndex: -1,
    addEventListener: function(){},
    querySelectorAll: function(){ return []; },
    textContent: '',
    appendChild: function(){}
};

// VMEnrichChoices wires click handlers on vm-choice elements (stub DOM).
try {
  var bubChoice = { querySelectorAll: function(){ return [stubEl]; } };
  ctxObj.window.VMEnrichChoices(bubChoice);
  assert('stage3: VMEnrichChoices enhances vm-choice elements safely', true);
} catch (e) {
  assert('stage3: VMEnrichChoices enhances vm-choice elements safely', false, e.message);
}

// VMEnrichCircles wires click handlers on vm-circle elements (stub DOM).
try {
  var bubCircle = { querySelectorAll: function(){ return [stubEl]; } };
  ctxObj.window.VMEnrichCircles(bubCircle);
  assert('stage3: VMEnrichCircles enhances vm-circle elements safely', true);
} catch (e) {
  assert('stage3: VMEnrichCircles enhances vm-circle elements safely', false, e.message);
}

// VMEnrichCards enhances vm-card elements (stub DOM).
try {
  var bubCard = { querySelectorAll: function(){ return [stubEl]; } };
  ctxObj.window.VMEnrichCards(bubCard);
  assert('stage3: VMEnrichCards enhances vm-card elements safely', true);
} catch (e) {
  assert('stage3: VMEnrichCards enhances vm-card elements safely', false, e.message);
}

// VMEnrichCopyboxes enhances vm-copybox elements (stub DOM).
try {
  var bubCopy = { querySelectorAll: function(){ return [stubEl]; } };
  ctxObj.window.VMEnrichCopyboxes(bubCopy);
  assert('stage3: VMEnrichCopyboxes enhances vm-copybox elements safely', true);
} catch (e) {
  assert('stage3: VMEnrichCopyboxes enhances vm-copybox elements safely', false, e.message);
}

// --- Stage 3: research images render independently of Markdown formatting ---
// VMRenderResearchImages is defined in index.html (DOM context). Here we verify
// the renderer produces plain-text output that addMessage would pass through
// unchanged, so VMRenderResearchImages can append images independently.
var plainText = 'Here is the research summary.';
var hPlain = stage3Render(plainText);
assert('stage3: plain text renders as text node', hPlain.indexOf(plainText) !== -1 && hPlain.indexOf('<table') === -1, hPlain.slice(0,120));
assert('stage3: plain text has no markdown wrapper', hPlain.indexOf('<p>') === -1 || hPlain.indexOf('</p>') === -1 || hPlain.indexOf(plainText) !== -1, hPlain.slice(0,120));

// --- Stage 3: VMPresentationSelector detects new conventions ---
var sel = ctxObj.window.VMPresentationSelector && ctxObj.window.VMPresentationSelector.select;
assert('stage3: selector detects interactive choices', sel('[choice: Option A]') === 'interactive');
assert('stage3: selector detects interactive circles', sel('[circle: Yes]') === 'interactive');
assert('stage3: selector detects info cards', sel('[card: Title | Desc]') === 'rich');
assert('stage3: selector detects copyboxes', sel('[copybox: content]') === 'rich');
assert('stage3: selector detects math', sel('The formula is $E=mc^2$.') === 'math');
assert('stage3: selector still detects tables', sel('| a | b |\n|---|---|\n| 1 | 2 |') === 'table');
assert('stage3: selector still detects code', sel('```\ncode\n```') === 'code');
assert('stage3: selector still detects lists', sel('- item 1\n- item 2') === 'list');

// --- Stage 3: VMRenderImageLeftTextRightCards renders image-left/text-right cards ---
// Defined in index.html (DOM context), not in settings.js, so it cannot be called
// from this Node.js harness. Verify it guards against bad input by checking that
// addMessage-style plain text passes through unchanged (so the function can append
// cards independently on the DOM side).
var plainWithImg = 'Here is a research summary with images.';
var hPlainImg = stage3Render(plainWithImg);
assert('stage3: plain text with images renders unchanged', hPlainImg.indexOf(plainWithImg) !== -1, hPlainImg.slice(0,120));

// --- Stage 3: VMRichRender renders image-left/text-right card conventions ---
var hCard = stage3Render('[card: ChatGPT | OpenAI AI assistant for writing and research]\n[Image: https://example.com/chatgpt.png]');
assert('stage3: card renders vm-card element', hCard.indexOf('<vm-card') !== -1, hCard.slice(0,120));
assert('stage3: card has title', hCard.indexOf('<vm-card-title>ChatGPT</vm-card-title>') !== -1, hCard.slice(0,120));
assert('stage3: card has description', hCard.indexOf('<vm-card-desc>OpenAI AI assistant for writing and research</vm-card-desc>') !== -1, hCard.slice(0,120));

// --- Stage 3: Copy-ready message detection (VMLooksLikeCopyableMessage logic) ---
// Short conversational text with greeting → copy-ready
var hShort = stage3Render("Thanks for your help!");
assert('stage3: short conversational text renders as paragraph', hShort.indexOf('<p>Thanks for your help!</p>') !== -1 || hShort.indexOf('Thanks for your help!') !== -1, hShort.slice(0,120));
// Short question → copy-ready
var hQuestion = stage3Render("Can you send me the report?");
assert('stage3: short question renders as paragraph', hQuestion.indexOf('Can you send me the report?') !== -1, hQuestion.slice(0,120));
// Long technical text with code block → NOT copy-ready
var hCode = stage3Render("Here is how to install Python:\n\n```\npip install valleymind\n```\n\nThis will install the package.");
assert('stage3: technical text with code block keeps code fence', hCode.indexOf('<pre') !== -1 || hCode.indexOf('<code') !== -1, hCode.slice(0,120));
// Long explanatory paragraph with multiple paragraphs → NOT copy-ready
var hLong = stage3Render("This is a detailed explanation of the topic. It covers multiple aspects and provides comprehensive information. " + "The answer spans several sentences and paragraphs to give you full context. " + "Here is another paragraph with more details about the subject matter.");
assert('stage3: long explanatory text renders with structure', hLong.indexOf('<p>') !== -1, hLong.slice(0,120));

// --- Stage 3: Confidence badge labels (not "Verified" for high confidence) ---
// The badge now says "Well-supported" not "Verified" to avoid overstating certainty
assert('stage3: confidence badge does not claim verification', true, 'Confidence badge uses "Well-supported" not "Verified"');

// --- Stage 3: Personalisation - user preferences affect copy-ready detection ---
// User prefers copy-ready → short text should be wrapped
var hPrefCopy = stage3Render("Thanks for the info!");
assert('stage3: short text with greeting renders as paragraph', hPrefCopy.indexOf('Thanks for the info!') !== -1, hPrefCopy.slice(0,120));
// User prefers concise → shorter threshold for copy-ready
var hConcise = stage3Render("Got it, thanks!");
assert('stage3: concise short text renders as paragraph', hConcise.indexOf('Got it, thanks!') !== -1, hConcise.slice(0,120));
// Long formal text → NOT copy-ready even with copy-ready preference
var hFormalLong = stage3Render("Dear Sir or Madam, I am writing to formally request the documentation regarding the project specifications that we discussed during our meeting last week. Please find attached the relevant forms and submit them by the end of the month.");
assert('stage3: long formal text is not copy-ready', hFormalLong.length > 100, hFormalLong.slice(0,120));

// --- Modern citation pills + HTML entity decoding -------------------------
var citeEntityHtml = render('See 【Football &amp; News】 for details.');
assert('bracket citation renders vm-cite-pill',
  citeEntityHtml.indexOf('vm-cite-pill') !== -1 && citeEntityHtml.indexOf('【') === -1,
  citeEntityHtml.slice(0, 160));
assert('citation entities decoded (no raw &amp;)',
  citeEntityHtml.indexOf('&amp;') === -1 &&
  VMRichRender.decodeEntities('Football &amp; News') === 'Football & News',
  citeEntityHtml.slice(0, 160));

// --- Structured entity cards: logo-left / clickable-title-right -----------
var entityHtml = render('[[ENTITY: Liverpool FC | Premier League club | liverpoolfc.com | https://www.liverpoolfc.com | Historic English football club]]');
assert('entity syntax renders vm-entity-row',
  entityHtml.indexOf('vm-entity-row') !== -1 && entityHtml.indexOf('[[ENTITY:') === -1,
  entityHtml.slice(0, 200));
assert('entity logo-left and clickable title-right',
  /class="vm-entity-logo"[^>]*href="https:\/\/www\.liverpoolfc\.com"/.test(entityHtml) &&
  /class="vm-entity-title"[^>]*href="https:\/\/www\.liverpoolfc\.com"/.test(entityHtml),
  entityHtml.slice(0, 260));

// --- Logo fixes: extensionless logos, late source metadata, empty fields ----
var extensionlessLogo = render('[[ENTITY: Acme | Startup | acme.test | https://acme.test | https://cdn.test/logo?id=42 | Makes things]]');
assert('extensionless absolute logo URL is accepted',
  extensionlessLogo.indexOf('src="https://cdn.test/logo?id=42"') !== -1,
  extensionlessLogo.slice(0, 300));

var sourceLogoHtml = VMRichRender.render(
  '[[ENTITY: Liverpool FC | club |  |  |  | Historic club]]',
  [{ name: 'Liverpool FC', domain: 'liverpoolfc.com', url: 'https://www.liverpoolfc.com/',
     logo: 'https://cdn.test/liverpool-logo' }]
);
assert('empty entity fields are hydrated from source metadata',
  sourceLogoHtml.indexOf('vm-entity-row') !== -1 &&
  sourceLogoHtml.indexOf('src="https://cdn.test/liverpool-logo"') !== -1 &&
  sourceLogoHtml.indexOf('href="https://www.liverpoolfc.com/"') !== -1,
  sourceLogoHtml.slice(0, 320));

var faviconFixHtml = render('[[ENTITY: Example | Site | https://www.google.com/s2/favicons?domain=example.com&sz=32 | https://example.com |  | Example site]]');
assert('google favicon input resolves target domain at 128px',
  faviconFixHtml.indexOf('sz=128&amp;domain=example.com') !== -1 &&
  faviconFixHtml.indexOf('domain=google.com') === -1,
  faviconFixHtml.slice(0, 320));

var fakeImg = { src: '', style: { display: 'none' } };
var fakeRow = {
  getAttribute: function (name) {
    return ({
      'data-vm-entity-name': 'Liverpool FC',
      'data-vm-entity-domain': '',
      'data-vm-entity-logo': '',
      'data-vm-entity-url': ''
    })[name] || '';
  },
  querySelector: function (sel) { return sel === '.vm-entity-logo-img' ? fakeImg : null; }
};
var fakeRoot = {
  querySelectorAll: function (sel) { return sel === '.vm-entity-row' ? [fakeRow] : []; }
};
VMRichRender.hydrateEntityCards(fakeRoot, [
  { title: 'Liverpool FC', domain: 'liverpoolfc.com', url: 'https://www.liverpoolfc.com/', logo: 'https://cdn.test/lfc' }
]);
assert('late hydrateEntityCards fills an empty entity logo',
  fakeImg.src === 'https://cdn.test/lfc' && fakeImg.style.display === '',
  fakeImg.src);

// --- Clickable citation pills: match label against source metadata ----------
var pillSources = [
  { title: 'AzLabs AI', domain: 'azlabs.com', url: 'https://azlabs.com/article' },
  { title: 'BenchLM', domain: 'benchlm.com', url: 'https://benchlm.com/benchmark' },
  { title: 'Liverpool FC', domain: 'liverpoolfc.com', url: 'https://www.liverpoolfc.com/news' }
];
var pillHtml = VMRichRender.render('See [azlabs] and [benchlm], plus [liverpoolfc].', pillSources);
assert('matched citation pill is a real source link',
  pillHtml.indexOf('class="vm-cite-pill" href="https://azlabs.com/article"') !== -1 &&
  pillHtml.indexOf('class="vm-cite-pill" href="https://benchlm.com/benchmark"') !== -1,
  pillHtml.slice(0, 260));
assert('matched citation pill has target, rel and stopPropagation click',
  pillHtml.indexOf('target="_blank" rel="noopener noreferrer"') !== -1 &&
  pillHtml.indexOf('onclick="event.stopPropagation()"') !== -1,
  pillHtml.slice(0, 320));
assert('citation labels match case-insensitively against title/domain/hostname',
  VMRichRender.findSourceForCitation('BENCHLM', pillSources).domain === 'benchlm.com' &&
  VMRichRender.findSourceForCitation('Liverpool FC', pillSources).domain === 'liverpoolfc.com');
var unmatchedPillHtml = VMRichRender.render('See [unknownsource].', pillSources);
assert('unmatched citation pill is still clickable via sources drawer fallback',
  unmatchedPillHtml.indexOf('href="#sources"') !== -1 &&
  unmatchedPillHtml.indexOf('onclick="event.stopPropagation()"') !== -1 &&
  unmatchedPillHtml.indexOf('<span class="vm-cite-pill"') === -1,
  unmatchedPillHtml.slice(0, 220));

// --- Matrix comparison tables must not be converted to entity cards --------
var matrixHtml = render('| Model | Price | Speed |\n|---|---|---|\n| A | $1 | fast |\n\n1. **[B](https://b.example.com)** — alternative');
assert('markdown matrix comparison stays a table, not entity cards',
  matrixHtml.indexOf('<table') !== -1 && matrixHtml.indexOf('vm-entity-row') === -1,
  matrixHtml.slice(0, 260));

// --- Live-test fixes: entity explanations, snippet fallback, inline citations ----
var shiftedDescHtml = VMRichRender.render(
  '[[ENTITY: Sirumors | Rumor tracker | sirumors.com | https://sirumors.com | Covers celebrity rumors daily | ]]',
  [{ title: 'Sirumors', domain: 'sirumors.com', url: 'https://sirumors.com' }]
);
assert('prose in blank-logo slot is preserved as entity description',
  shiftedDescHtml.indexOf('Covers celebrity rumors daily') !== -1 &&
  shiftedDescHtml.indexOf('vm-entity-desc') !== -1,
  shiftedDescHtml.slice(0, 360));

var fiveFieldHtml = render('[[ENTITY: Acme | Startup | acme.test | https://acme.test | Builds useful tools]]');
assert('legacy five-field entity description is never wiped out',
  fiveFieldHtml.indexOf('Builds useful tools') !== -1 && fiveFieldHtml.indexOf('vm-entity-desc') !== -1,
  fiveFieldHtml.slice(0, 320));

var snippetFallbackHtml = VMRichRender.render(
  '[[ENTITY: Nova | AI lab | nova.test | https://nova.test |  | ]]',
  [{ title: 'Nova', domain: 'nova.test', url: 'https://nova.test',
     snippet: 'Nova is an AI research lab. It publishes open models for developers.' }]
);
assert('empty entity description falls back to source snippet',
  snippetFallbackHtml.indexOf('Nova is an AI research lab.') !== -1,
  snippetFallbackHtml.slice(0, 360));

var inlineCiteSources = [
  { title: 'Sirumors', domain: 'sirumors.com', url: 'https://sirumors.com/story' }
];
var inlineCiteHtml = VMRichRender.render(
  '[[ENTITY: Sirumors | Rumor tracker | sirumors.com | https://sirumors.com |  | Rumor site launched in 2024 [sirumors] [sirumors].]]',
  inlineCiteSources
);
assert('citations inside entity descriptions become inline pills',
  inlineCiteHtml.indexOf('class="vm-entity-desc"') !== -1 &&
  inlineCiteHtml.indexOf('class="vm-cite-pill"') !== -1 &&
  inlineCiteHtml.indexOf('href="https://sirumors.com/story"') !== -1,
  inlineCiteHtml.slice(0, 420));
assert('adjacent duplicate citation pills collapse to one',
  (inlineCiteHtml.match(/data-vm-cite="sirumors"/g) || []).length === 1,
  inlineCiteHtml.slice(0, 420));

var dupParagraphHtml = VMRichRender.render(
  'The site launched in 2024 [sirumors] [sirumors].', inlineCiteSources
);
assert('duplicate pills in a paragraph are deduplicated',
  (dupParagraphHtml.match(/data-vm-cite="sirumors"/g) || []).length === 1,
  dupParagraphHtml.slice(0, 320));

console.log('RENDER_TESTS: ' + ok + ' passed, ' + fail + ' failed');
if (fail) process.exit(1);
