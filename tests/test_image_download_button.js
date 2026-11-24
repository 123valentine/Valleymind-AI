// Tests that generated image cards use the shared icon-only download button
// and show a circular spinner during the WebView/fetch/blob handoff.
const fs = require('fs');
const vm = require('vm');

let ok = 0, fail = 0;
function assert(name, cond, detail) {
  if (cond) { console.log('OK: ' + name); ok++; }
  else { console.log('FAIL: ' + name + (detail ? ' :: ' + String(detail).slice(0, 240) : '')); fail++; }
}
function escapeHtml(text) {
  return String(text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function marked(src) {
  const text = String(src || '');
  return text.split(/\n\s*\n/).map((p) => '<p>' + escapeHtml(p).replace(/\n/g, '<br>') + '</p>').join('\n');
}
marked.parse = marked;
function makeWalker() { return { nextNode: function () { return null; } }; }
function StubDOMParser() {}
StubDOMParser.prototype.parseFromString = function (html) {
  return { body: { innerHTML: html, createTreeWalker: makeWalker }, createTreeWalker: makeWalker };
};

const fetchCalls = [];
function makeElement(tag) {
  const el = {
    tagName: String(tag || '').toUpperCase(),
    children: [],
    style: { pointerEvents: '' },
    attrs: {},
    className: '',
    innerHTML: '',
    textContent: '',
    value: '',
    rows: 0,
    disabled: false,
    parentNode: null,
    classList: {
      flags: {},
      add(name) { this.flags[name] = true; },
      remove(name) { delete this.flags[name]; },
      contains(name) { return !!this.flags[name]; }
    },
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    removeChild(child) {
      const idx = this.children.indexOf(child);
      if (idx !== -1) this.children.splice(idx, 1);
      child.parentNode = null;
      return child;
    },
    remove() { if (this.parentNode) this.parentNode.removeChild(this); },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : ''; },
    querySelectorAll(sel) {
      const found = [];
      function walk(node) {
        for (const child of node.children || []) {
          if ((sel.indexOf('data-download-url') !== -1 && child.getAttribute('data-download-url')) ||
              (sel.indexOf('data-copy-id') !== -1 && child.getAttribute('data-copy-id'))) found.push(child);
          walk(child);
        }
      }
      walk(this);
      return found;
    },
    contains(node) { return this.children.some((child) => child === node || (child.contains && child.contains(node))); },
    focus() {},
    select() {},
    setSelectionRange() {},
    click() {}
  };
  return el;
}

const messages = makeElement('div');
const chatInput = makeElement('textarea');
const documentStub = {
  readyState: 'complete',
  createTreeWalker: makeWalker,
  addEventListener: function () {},
  createElement: function (tag) { return makeElement(tag); },
  body: makeElement('body'),
  documentElement: makeElement('html'),
  getElementById: function (id) {
    if (id === 'messages') return messages;
    if (id === 'chatInput') return chatInput;
    return null;
  },
  execCommand: function () { return true; }
};

const win = {
  DOMParser: StubDOMParser,
  marked: marked,
  document: documentStub,
  navigator: {},
  URL: {
    createObjectURL: function () { return 'blob:vm-test'; },
    revokeObjectURL: function () {}
  },
  console: console,
  Promise: Promise,
  Object: Object,
  Date: Date,
  setTimeout: function (fn, ms) { return Number(ms) >= 30000 ? 0 : setTimeout(fn, ms); },
  clearTimeout: clearTimeout,
  NodeFilter: { SHOW_ELEMENT: 1, SHOW_COMMENT: 128 },
  fetch: function (url, opts) {
    fetchCalls.push({ url, opts });
    return Promise.resolve({ ok: true, blob: function () { return Promise.resolve({ size: 8 }); } });
  },
  open: function () {}
};
const ctx = {
  window: win,
  document: documentStub,
  DOMParser: StubDOMParser,
  marked: marked,
  navigator: win.navigator,
  URL: win.URL,
  console: console,
  Promise: Promise,
  Object: Object,
  Date: Date,
  setTimeout: win.setTimeout,
  clearTimeout: clearTimeout,
  NodeFilter: win.NodeFilter
};
vm.createContext(ctx);
const settingsSrc = fs.readFileSync('static/settings.js', 'utf8');
const addonSrc = fs.readFileSync('static/vm_richrender.js', 'utf8');
vm.runInContext(settingsSrc, ctx);
vm.runInContext(addonSrc, ctx);

const html = fs.readFileSync('index.html', 'utf8');
const fnMatch = html.match(/function renderImageResultCard\([\s\S]*?\n\}/);
assert('generated image card renderer found', !!fnMatch);
vm.runInContext(fnMatch ? fnMatch[0] : '', ctx);

ctx.removeImageGenSkeleton = function () {};
ctx._imgGenStartTime = Date.now() - 9000; // skip the deliberate 8s UI wait

function findNode(node, predicate) {
  if (!node) return null;
  if (predicate(node)) return node;
  for (const child of node.children || []) {
    const found = findNode(child, predicate);
    if (found) return found;
  }
  return null;
}

(async function () {
  ctx.renderImageResultCard('/api/images/gen-1.png', 'A lighthouse at dawn', 'revised');
  for (let i = 0; i < 100 && messages.children.length === 0; i++) {
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  const wrapper = messages.children[0];
  const button = findNode(wrapper, (node) => String(node.className || '').indexOf('btn-download') !== -1);
  assert('generated image card renders a button, not a text link', !!button && button.tagName === 'BUTTON');
  assert('generated image card download is icon-only', !!button &&
    button.getAttribute('title') === 'Download' &&
    button.getAttribute('aria-label') === 'Download' &&
    button.innerHTML.indexOf('vm-download-icon') !== -1 &&
    button.innerHTML.indexOf('>Download<') === -1,
    button && button.innerHTML);
  assert('generated image card carries signed-style download target', !!button &&
    button.getAttribute('data-download-url') === '/api/images/gen-1.png' &&
    button.getAttribute('data-download-name') === 'valleymind_image.png',
    button && JSON.stringify(button.attrs));

  const originalHtml = button.innerHTML;
  ctx.window.VMFormatRichBubble(wrapper);
  fetchCalls.length = 0;
  button.onclick({ stopPropagation() {} });
  assert('generated image click swaps icon for circular spinner',
    button.innerHTML.indexOf('vm-download-spinner') !== -1 &&
    button.classList.contains('vm-downloading') &&
    button.style.pointerEvents === 'none' &&
    button.getAttribute('aria-busy') === 'true',
    button.innerHTML);
  for (let i = 0; i < 100 && button.__vmDownloadActive; i++) {
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  assert('generated image restores download icon after settle',
    button.innerHTML === originalHtml &&
    !button.classList.contains('vm-downloading') &&
    button.style.pointerEvents === '' &&
    button.getAttribute('aria-busy') === 'false',
    button.innerHTML);
  assert('generated image spinner still performs download',
    fetchCalls.length === 1 && fetchCalls[0].url === '/api/images/gen-1.png', JSON.stringify(fetchCalls));

  assert('settings media image cards use icon-only download button',
    /vm-icon-download vm-media-download[\s\S]{0,500}VMDownloadIconSvg\(\)/.test(settingsSrc) &&
    settingsSrc.indexOf('>DL</a>') === -1);
  assert('generated image card source no longer contains visible Download text',
    !/btn-download[\s\S]{0,900}>Download</.test(html));

  console.log('IMAGE_DOWNLOAD_BUTTON_TESTS: ' + ok + ' passed, ' + fail + ' failed');
  if (fail) process.exit(1);
})().catch((err) => {
  console.log('FAIL: image download button harness threw :: ' + (err && err.stack ? err.stack : err));
  process.exit(1);
});
