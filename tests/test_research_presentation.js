// Verification for the web-research presentation upgrade:
//  - one clean answer / no raw citation or math artifacts
//  - responsive horizontal media cards with fallback + lightbox + source link
//  - context-aware follow-up chips (never shopping boilerplate for movies)
const fs = require('fs');
const vm = require('vm');

let ok = 0, fail = 0;
function assert(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0, 240) : '')); fail++; }
}

const html = fs.readFileSync('index.html', 'utf8');
const settingsSrc = fs.readFileSync('static/settings.js', 'utf8');
const richSrc = fs.readFileSync('static/vm_richrender.js', 'utf8');

function sliceBetween(startMarker, endMarker) {
  const start = html.indexOf(startMarker);
  const end = html.indexOf(endMarker, start);
  return start >= 0 && end > start ? html.slice(start, end) : '';
}

// ── DOM stubs ─────────────────────────────────────────────────────────────────
function makeNode(tag) {
  const node = {
    tagName: String(tag || '').toUpperCase(),
    className: '', textContent: '', type: '', href: '', src: '', alt: '',
    rel: '', title: '', value: '',
    style: { cssText: '', display: '' },
    dataset: {}, children: [], attrs: {}, onclick: null, onerror: null,
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    setAttribute(key, value) { this.attrs[key] = String(value); },
    getAttribute(key) { return this.attrs[key] || ''; },
    _all() { return this.children.reduce((acc, c) => acc.concat(c, c._all ? c._all() : []), []); },
    querySelector(sel) {
      if (sel.charAt(0) !== '.') return null;
      const cls = sel.slice(1);
      if (this.className.split(/\s+/).indexOf(cls) !== -1) return this;
      return this._all().find((c) => c.className && c.className.split(/\s+/).indexOf(cls) !== -1) || null;
    },
    querySelectorAll(sel) {
      const cls = sel.replace(/^\./, '');
      return this._all().filter((c) => c.className && c.className.split(/\s+/).indexOf(cls) !== -1);
    }
  };
  return node;
}

const viewerOpens = [];
const windowStub = {
  innerWidth: 390,
  openImageViewer(src) { viewerOpens.push(src); }
};
const documentStub = { createElement(tag) { return makeNode(tag); } };
const mediaSrc = sliceBetween('function VMResearchImageIsLandscape(', 'function VMRenderImageLeftTextRightCards(');
assert('media renderer source extracted', mediaSrc.indexOf('function VMRenderResearchImages(') !== -1);

const mediaCtx = {
  window: windowStub,
  document: documentStub,
  VMDecodeHtmlEntities(s) { return String(s == null ? '' : s); },
  VMIsGenericResearchImage() { return false; },
  parseFloat, String, Array, Object, isNaN
};
vm.createContext(mediaCtx);
vm.runInContext(mediaSrc + '\nthis.VMRenderResearchImages = VMRenderResearchImages;', mediaCtx);

const bubble = makeNode('div');
bubble.className = 'bubble';
const wrapper = makeNode('div');
wrapper.className = 'assistant-message';
wrapper.appendChild(bubble);

const sources = [
  {
    title: 'The Film', domain: 'imdb.com', url: 'https://www.imdb.com/title/tt123/',
    image: 'https://images.example/poster.jpg', description: 'Verified cast and release details.',
    year: '2026', type: 'movie'
  },
  {
    title: 'Behind the Scenes', domain: 'youtube.com', url: 'https://www.youtube.com/watch?v=abc',
    image: 'https://images.example/still.jpg', snippet: 'Official trailer still.',
    type: 'video', media_type: 'trailer'
  },
  {
    title: 'Wide Still', domain: 'news.example', url: 'https://news.example/still',
    image: 'https://images.example/wide.jpg', description: 'A verified wide still.',
    aspect_ratio: '1.78'
  }
];

mediaCtx.VMRenderResearchImages(wrapper, sources);
const list = bubble.querySelector('.vm-research-images');
assert('media cards render once in a vertical list', !!list && list.children.length === 3);
assert('no side-by-side strip or scroll carousel',
  list && !/Side-by-side comparison|overflowX:auto/.test(list.textContent + list.style.cssText));

const firstCard = list && list.children[0];
assert('card is horizontal with max-width and subtle divider',
  !!firstCard && /flex-direction:row/.test(firstCard.style.cssText) &&
  /max-width:100%/.test(firstCard.style.cssText) &&
  /1px solid rgba\(255,255,255,0\.08\)/.test(firstCard.style.cssText),
  firstCard && firstCard.style.cssText);
assert('poster uses 2:3 proportions without stretching',
  !!firstCard && /width:118px/.test(firstCard.style.cssText) === false &&
  /aspect-ratio:2\/3/.test(firstCard.children[0].style.cssText) &&
  /object-fit:cover/.test(firstCard.children[0].children[0].style.cssText),
  firstCard && firstCard.children[0].style.cssText);
assert('landscape video still uses 16:9',
  list && /aspect-ratio:16\/9/.test(list.children[1].children[0].style.cssText),
  list && list.children[1].children[0].style.cssText);
assert('explicit aspect ratio also selects 16:9',
  list && /aspect-ratio:16\/9/.test(list.children[2].children[0].style.cssText),
  list && list.children[2].children[0].style.cssText);

const firstImg = firstCard && firstCard.children[0].children[0];
assert('original remote image URL and source link are preserved',
  firstImg && firstImg.src === 'https://images.example/poster.jpg' &&
  firstCard.children[1].children.some((el) => el.href === 'https://www.imdb.com/title/tt123/'),
  firstCard && firstCard.children[1].children.map((el) => el.href));
assert('source link opens the original URL in a new tab',
  firstCard && firstCard.children[1].children.some((el) => el.target === '_blank' && el.rel === 'noopener noreferrer'));

if (firstCard && firstCard.children[0].onclick) firstCard.children[0].onclick({ stopPropagation() {}, preventDefault() {} });
assert('tap-to-preview opens the original image in the lightbox',
  viewerOpens.indexOf('https://images.example/poster.jpg') !== -1, viewerOpens);

if (firstImg && firstImg.onerror) firstImg.onerror();
assert('broken image keeps the card and shows a graceful fallback',
  firstCard && firstCard.parentNode === list &&
  firstCard.children[0].querySelector('.vm-media-fallback') !== null &&
  firstImg.style.display === 'none');

// ── Inline artifact cleanup + multi-domain citation pills ────────────────────
const cleanSrc = sliceBetween('function VMCleanInlineResearchArtifacts(', 'function renderInlineCitations(');
const cleanCtx = { String };
vm.createContext(cleanCtx);
vm.runInContext(cleanSrc + '\nthis.clean = VMCleanInlineResearchArtifacts;', cleanCtx);
const cleaned = cleanCtx.clean('151 milliongross(thearticlecites"151"and"$60") and $75$ [a.com; b.com].');
assert('raw article-cites note is gone', cleaned.indexOf('thearticlecites') === -1 && cleaned.indexOf('151 million gross') !== -1, cleaned);
assert('math-wrapped currency is plain', cleaned.indexOf('$75$') === -1 && cleaned.indexOf('$75') !== -1, cleaned);

const marked = function (src) { return '<p>' + String(src || '') + '</p>'; };
marked.parse = marked;
function StubDOMParser() {}
StubDOMParser.prototype.parseFromString = function (src) {
  const noWalker = { nextNode() { return null; } };
  return {
    body: { innerHTML: src, createTreeWalker() { return noWalker; } },
    createTreeWalker() { return noWalker; }
  };
};
const settingsCtx = {
  window: { marked, DOMParser: StubDOMParser },
  document: { addEventListener() {}, createTreeWalker() { return { nextNode() { return null; } }; } },
  DOMParser: StubDOMParser,
  marked, console, Promise, Object, String, Array, Date,
  setTimeout, clearTimeout,
  NodeFilter: { SHOW_ELEMENT: 1, SHOW_COMMENT: 128 }
};
vm.createContext(settingsCtx);
vm.runInContext(settingsSrc, settingsCtx);
const pillHtml = settingsCtx.window.VMRichRender.render(
  'Evidence [azlabs.com; benchlm.com] supports this.',
  [
    { title: 'AzLabs AI', domain: 'azlabs.com', url: 'https://azlabs.com/article' },
    { title: 'BenchLM', domain: 'benchlm.com', url: 'https://benchlm.com/benchmark' }
  ]
);
assert('bracket dump becomes two clickable source pills',
  (pillHtml.match(/vm-cite-pill/g) || []).length === 2 &&
  pillHtml.indexOf('https://azlabs.com/article') !== -1 &&
  pillHtml.indexOf('https://benchlm.com/benchmark') !== -1 &&
  pillHtml.indexOf('[azlabs.com; benchlm.com]') === -1,
  pillHtml);

// vm_richrender.js loads after settings.js in the app and early-returns without
// window.VMRichRender, so install it into the same context to mirror production.
vm.runInContext(richSrc, settingsCtx);
assert('renderer also strips garbled research notes',
  typeof settingsCtx.window.VMCleanResearchArtifacts === 'function' &&
  settingsCtx.window.VMCleanResearchArtifacts('Gross(thearticlecites"151"and"$60")').indexOf('thearticlecites') === -1);

// ── Context-aware follow-up chips ────────────────────────────────────────────
const chipSrc = sliceBetween('function VMAddProactiveSuggestions(', '/* Append the small, muted AI disclaimer');
assert('suggestion function source extracted', chipSrc.indexOf('Explore the cast') !== -1);
const chipCtx = {
  window: { _userPreferences: {} },
  document: documentStub,
  Array, Object, String, RegExp
};
vm.createContext(chipCtx);
vm.runInContext(chipSrc + '\nthis.add = VMAddProactiveSuggestions;', chipCtx);

function chipsFor(text) {
  const target = makeNode('div');
  target.className = 'bubble';
  chipCtx.add(target, text, '');
  const container = target.querySelector('.vm-suggestion-chips');
  return container ? container.children.map((b) => b.textContent) : [];
}

const movieChips = chipsFor(
  'I found that **The Film** stars a well-known cast and the trailer is online. Sources: [imdb.com].'
);
assert('movie answer suggests cast and trailer, not pricing',
  movieChips.indexOf('Explore the cast') !== -1 &&
  movieChips.indexOf('Find the trailer') !== -1 &&
  !movieChips.some((c) => /pricing|price|cost/i.test(c)),
  movieChips);

const sceneChips = chipsFor('Here are the verified scenes in **The Film** and the clip descriptions.');
assert('scene-focused answer offers specific scenes', sceneChips.indexOf('Ask about specific scenes') !== -1, sceneChips);

const productChips = chipsFor('The **Phone X** price is $699 and its specs include a fast chip.');
assert('pricing chip only appears for genuine product pricing',
  productChips.some((c) => /pricing/i.test(c)), productChips);

assert('shopping boilerplate label is absent from the app',
  html.indexOf('"Check current pricing"') === -1);
assert('chat rail clips horizontal overflow',
  /#messages,\s*\.chat-messages\{\s*overflow-x:hidden;\s*\}/.test(html));

console.log('\nRESEARCH_PRESENTATION_TESTS: ' + ok + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
